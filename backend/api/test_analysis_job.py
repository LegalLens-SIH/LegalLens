from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import Headers, UploadFile

from backend.api import ocr as ocr_module
from backend.api.auth import official_user
from backend.main import app


class CapturingBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, function, *args, **kwargs):
        self.tasks.append((function, args, kwargs))


def test_single_image_analysis_job_schedules_existing_scan_worker_with_mode_and_capture(monkeypatch):
    background_tasks = CapturingBackgroundTasks()
    upload = UploadFile(
        file=BytesIO(b"validated-by-route"),
        filename="label.png",
        headers=Headers({"content-type": "image/png"}),
    )
    user = {"_id": "job-test-user", "role": "official"}

    result = asyncio.run(ocr_module.start_analysis_job(
        background_tasks=background_tasks,
        file=[upload],
        product_name="Label",
        category="General",
        manufacturer="ACME",
        image_sides='["Front"]',
        capture_quality_requested="[true]",
        ocr_mode="high_accuracy",
        user=user,
    ))

    assert result["status"] == "running"
    assert result["imageCount"] == 1
    function, args, _kwargs = background_tasks.tasks[0]
    assert function is ocr_module._run_analysis_job_in_worker
    assert args[1:4] == (b"validated-by-route", "label.png", "image/png")
    assert args[-3:] == ("high_accuracy", "Front", True)
    ocr_module._analysis_jobs.pop(result["id"], None)


def test_analysis_job_rejects_unknown_ocr_mode_before_scheduling():
    background_tasks = CapturingBackgroundTasks()
    upload = UploadFile(file=BytesIO(b"image"), filename="label.png")

    with pytest.raises(HTTPException) as error:
        asyncio.run(ocr_module.start_analysis_job(
            background_tasks=background_tasks,
            file=[upload],
            image_sides="[]",
            capture_quality_requested="[]",
            ocr_mode="fast",
            user={"_id": "job-test-user", "role": "official"},
        ))

    assert error.value.status_code == 400
    assert background_tasks.tasks == []


def test_single_image_http_request_returns_pollable_job_without_waiting_for_ocr(monkeypatch):
    official = {"_id": "job-http-test-user", "role": "official"}
    scheduled = []
    monkeypatch.setattr(ocr_module, "_run_analysis_job_in_worker", lambda *args: scheduled.append(args))
    app.dependency_overrides[official_user] = lambda: official
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/analysis-jobs",
                files={"file": ("label.png", b"image bytes", "image/png")},
                data={"ocr_mode": "standard", "image_sides": '["Front"]'},
            )
            assert response.status_code == 200, response.text
            job = response.json()
            status = client.get(f"/api/analysis-jobs/{job['id']}")

        assert status.status_code == 200
        assert status.json()["status"] == "running"
        assert status.json()["imageCount"] == 1
        assert len(scheduled) == 1
        assert scheduled[0][1:4] == (b"image bytes", "label.png", "image/png")
    finally:
        app.dependency_overrides.pop(official_user, None)
        for job_id in [key for key, value in ocr_module._analysis_jobs.items() if value.get("userId") == official["_id"]]:
            ocr_module._analysis_jobs.pop(job_id, None)
