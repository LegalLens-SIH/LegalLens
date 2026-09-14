"""Tests for the official review workflow: manufacturer submit -> official
review-queue/detail/image/decision, through the real FastAPI app, the real
MongoDB (+ GridFS) connection, and the real ComplianceEngine - mirroring
test_manufacturer.py's exact conventions (dependency_overrides for auth,
only PaddleOCRService.run monkeypatched, manual cleanup in a finally block).

Regression coverage for the pre-existing, unmodified manufacturer-owned
image route (still ownership-scoped) already lives in
test_self_check_image_endpoint_requires_ownership in test_manufacturer.py -
not duplicated here.
"""
from __future__ import annotations

from bson import ObjectId
from fastapi.testclient import TestClient

from backend.api import ocr as ocr_module
from backend.api.auth import current_user, manufacturer_user, official_user
from backend.api.test_manufacturer import FAKE_MANUFACTURER, IMAGE_A_BYTES, _echo_ocr_run, _cleanup
from backend.database import get_database
from backend.main import app

FAKE_OFFICIAL = {
    "_id": ObjectId(),
    "role": "official",
    "fullName": "Inspector Test",
    "email": "test-official-focused@example.com",
}
OTHER_OFFICIAL = {
    "_id": ObjectId(),
    "role": "official",
    "fullName": "Inspector Two",
    "email": "test-official-two@example.com",
}


def _create_revision(client: TestClient, product_name: str = "Acme Cookies") -> dict:
    app.dependency_overrides[manufacturer_user] = lambda: FAKE_MANUFACTURER
    response = client.post(
        "/api/manufacturer/self-check",
        files={"file": ("label.png", IMAGE_A_BYTES, "image/png")},
        data={"product_id": "", "product_name": product_name},
    )
    assert response.status_code == 200, response.text
    return response.json()["revision"]


def _seed_manufacturer_user(db) -> None:
    """FAKE_MANUFACTURER is only ever supplied via dependency_overrides, never
    a real signup - but official.py's review-queue/detail identity lookup
    reads the real `users` collection (manufacturerId -> fullName/company/
    email), so tests asserting on that identity need a real matching doc."""
    db.users.update_one({"_id": FAKE_MANUFACTURER["_id"]}, {"$set": {**FAKE_MANUFACTURER, "companyName": "Acme Foods Pvt Ltd"}}, upsert=True)


def _cleanup_manufacturer_user(db) -> None:
    db.users.delete_one({"_id": FAKE_MANUFACTURER["_id"]})


def _submit(client: TestClient, revision_id: str) -> dict:
    app.dependency_overrides[manufacturer_user] = lambda: FAKE_MANUFACTURER
    response = client.post(f"/api/manufacturer/revisions/{revision_id}/submit")
    assert response.status_code == 200, response.text
    return response.json()["revision"]


def test_review_queue_lists_submitted_revision_with_manufacturer_identity_and_excludes_unsubmitted(monkeypatch):
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    try:
        with TestClient(app) as client:
            try:
                _seed_manufacturer_user(get_database())
                unsubmitted = _create_revision(client, "Draft Only Product")
                submitted = _create_revision(client, "Submitted Product")
                _submit(client, submitted["revisionId"])

                app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
                response = client.get("/api/official/review-queue")
                assert response.status_code == 200, response.text
                items = response.json()["items"]
                ids = {item["revisionId"] for item in items}
                assert submitted["revisionId"] in ids
                assert unsubmitted["revisionId"] not in ids

                queued = next(item for item in items if item["revisionId"] == submitted["revisionId"])
                assert queued["reviewStatus"] == "PENDING_OFFICIAL_REVIEW"
                assert queued["manufacturerName"] == FAKE_MANUFACTURER["fullName"]
                assert queued["manufacturerEmail"] == FAKE_MANUFACTURER["email"]
                assert queued["manufacturerCompany"] == "Acme Foods Pvt Ltd"
                assert queued["sourceImageUrl"] == f"/api/official/revisions/{submitted['revisionId']}/image"
            finally:
                app.dependency_overrides.pop(official_user, None)
                _cleanup(get_database())
                _cleanup_manufacturer_user(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)


def test_unsubmitted_revision_is_invisible_to_officials_everywhere(monkeypatch):
    """A private, not-yet-submitted self-check must never be reachable via
    the detail or image endpoints either, not just excluded from the queue -
    an official must not be able to browse into it by guessing the id."""
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    try:
        with TestClient(app) as client:
            try:
                draft = _create_revision(client, "Private Draft")

                app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
                detail = client.get(f"/api/official/revisions/{draft['revisionId']}")
                assert detail.status_code == 404
                image = client.get(f"/api/official/revisions/{draft['revisionId']}/image")
                assert image.status_code == 404
            finally:
                app.dependency_overrides.pop(official_user, None)
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)


def test_detail_read_transitions_pending_to_under_review_without_regressing_a_decided_case(monkeypatch):
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    try:
        with TestClient(app) as client:
            try:
                revision = _create_revision(client)
                _submit(client, revision["revisionId"])

                app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
                first_read = client.get(f"/api/official/revisions/{revision['revisionId']}")
                assert first_read.json()["revision"]["reviewStatus"] == "UNDER_OFFICIAL_REVIEW"

                decision = client.post(
                    f"/api/official/revisions/{revision['revisionId']}/decision",
                    json={"action": "APPROVED", "remarks": "Meets all requirements."},
                )
                assert decision.status_code == 200, decision.text
                assert decision.json()["revision"]["reviewStatus"] == "DECISION_RECORDED"

                # Re-opening the case detail after a decision must never
                # regress reviewStatus back to UNDER_OFFICIAL_REVIEW.
                second_read = client.get(f"/api/official/revisions/{revision['revisionId']}")
                assert second_read.json()["revision"]["reviewStatus"] == "DECISION_RECORDED"
            finally:
                app.dependency_overrides.pop(official_user, None)
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)


def test_decisions_accumulate_in_history_without_overwrite_and_latest_wins_for_official_decision(monkeypatch):
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    try:
        with TestClient(app) as client:
            try:
                revision = _create_revision(client)
                _submit(client, revision["revisionId"])

                app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
                first = client.post(
                    f"/api/official/revisions/{revision['revisionId']}/decision",
                    json={"action": "NEEDS_CORRECTION", "remarks": "Net quantity illegible."},
                )
                assert first.status_code == 200, first.text

                app.dependency_overrides[official_user] = lambda: OTHER_OFFICIAL
                second = client.post(
                    f"/api/official/revisions/{revision['revisionId']}/decision",
                    json={"action": "APPROVED", "remarks": "Corrected label verified."},
                )
                assert second.status_code == 200, second.text
                body = second.json()["revision"]

                assert len(body["decisionHistory"]) == 2
                assert [entry["action"] for entry in body["decisionHistory"]] == ["NEEDS_CORRECTION", "APPROVED"]
                assert body["officialDecision"]["action"] == "APPROVED"
                assert body["officialDecision"]["officialId"] == str(OTHER_OFFICIAL["_id"])
                assert body["decisionHistory"][0]["officialId"] == str(FAKE_OFFICIAL["_id"])
            finally:
                app.dependency_overrides.pop(official_user, None)
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)


def test_manufacturer_sees_official_decision_read_only_via_existing_revisions_endpoint(monkeypatch):
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    try:
        with TestClient(app) as client:
            try:
                revision = _create_revision(client)
                _submit(client, revision["revisionId"])

                app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
                client.post(
                    f"/api/official/revisions/{revision['revisionId']}/decision",
                    json={"action": "REJECTED", "remarks": "MRP declaration missing."},
                )

                app.dependency_overrides[manufacturer_user] = lambda: FAKE_MANUFACTURER
                listing = client.get("/api/manufacturer/revisions")
                assert listing.status_code == 200, listing.text
                mine = next(item for item in listing.json()["items"] if item["revisionId"] == revision["revisionId"])
                assert mine["reviewStatus"] == "DECISION_RECORDED"
                assert mine["officialDecision"]["action"] == "REJECTED"
                assert mine["officialDecision"]["remarks"] == "MRP declaration missing."
            finally:
                app.dependency_overrides.pop(official_user, None)
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)


def test_submit_is_guarded_against_duplicate_pending_submission(monkeypatch):
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    try:
        with TestClient(app) as client:
            try:
                revision = _create_revision(client)
                _submit(client, revision["revisionId"])
                app.dependency_overrides[manufacturer_user] = lambda: FAKE_MANUFACTURER
                again = client.post(f"/api/manufacturer/revisions/{revision['revisionId']}/submit")
                assert again.status_code == 409
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)


def test_official_image_endpoint_streams_the_submitted_revisions_bytes(monkeypatch):
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    try:
        with TestClient(app) as client:
            try:
                revision = _create_revision(client)
                _submit(client, revision["revisionId"])

                app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
                image = client.get(f"/api/official/revisions/{revision['revisionId']}/image")
                assert image.status_code == 200
                assert image.content == IMAGE_A_BYTES
            finally:
                app.dependency_overrides.pop(official_user, None)
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)


def test_manufacturer_forbidden_from_every_official_endpoint(monkeypatch):
    """Backend-enforced authorization, not merely a hidden UI button: a
    manufacturer session must get 403 from every /api/official/* route.

    Overrides `current_user` (the base session dependency), NOT
    `official_user`/`manufacturer_user` directly - overriding the
    role-specific dependency would bypass the real require_role() check
    entirely and could never prove a 403 actually fires. This is the one
    real authorization boundary in the whole test file that must exercise
    the genuine role-check logic, not a stand-in for it."""
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    try:
        with TestClient(app) as client:
            try:
                revision = _create_revision(client)
                _submit(client, revision["revisionId"])

                app.dependency_overrides.pop(manufacturer_user, None)
                app.dependency_overrides[current_user] = lambda: FAKE_MANUFACTURER
                assert client.get("/api/official/review-queue").status_code == 403
                assert client.get(f"/api/official/revisions/{revision['revisionId']}").status_code == 403
                assert client.get(f"/api/official/revisions/{revision['revisionId']}/image").status_code == 403
                assert client.post(
                    f"/api/official/revisions/{revision['revisionId']}/decision",
                    json={"action": "APPROVED", "remarks": "n/a"},
                ).status_code == 403
            finally:
                app.dependency_overrides.pop(current_user, None)
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)


def test_official_forbidden_from_manufacturer_submit_endpoint(monkeypatch):
    """Same real-role-check reasoning as above, in the other direction."""
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    try:
        with TestClient(app) as client:
            try:
                revision = _create_revision(client)
                app.dependency_overrides.pop(manufacturer_user, None)
                app.dependency_overrides[current_user] = lambda: FAKE_OFFICIAL
                response = client.post(f"/api/manufacturer/revisions/{revision['revisionId']}/submit")
                assert response.status_code == 403
            finally:
                app.dependency_overrides.pop(current_user, None)
                app.dependency_overrides[manufacturer_user] = lambda: FAKE_MANUFACTURER
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)
