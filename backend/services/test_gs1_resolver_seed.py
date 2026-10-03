from __future__ import annotations

import json
import sys

import pytest

from scripts import seed_gs1_resolver_ce_fixture as seeder


class FakeResponse:
    def __init__(self, status_code=201, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {
            "entry": "01_10012345678902",
            "result": {"response_status": 201, "data": "synthetic record accepted"},
        }
        self.headers = {
            "content-length": str(len(json.dumps(self._body))),
            "content-type": "application/json",
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def iter_bytes(self):
        yield json.dumps(self._body).encode("utf-8")


class FakeClient:
    calls = []
    options = []
    response = FakeResponse()

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.options.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


def test_apply_posts_only_documented_payload_and_validates_response(monkeypatch, capsys):
    FakeClient.calls = []
    FakeClient.response = FakeResponse()
    monkeypatch.setattr(seeder.httpx, "Client", FakeClient)
    monkeypatch.setattr(sys, "argv", ["seed_gs1_resolver_ce_fixture", "--apply"])
    monkeypatch.setenv("GS1_RESOLVER_SESSION_TOKEN", "T" * 40)
    monkeypatch.setenv("GS1_RESOLVER_BASE_URL", "http://localhost:8080")

    assert seeder.main() == 0
    method, url, request = FakeClient.calls[0]
    assert method == "POST"
    assert url == "http://localhost:8080/api/new"
    assert request["json"]["anchor"] == "/01/10012345678902"
    assert request["headers"]["Authorization"] == "Bearer " + ("T" * 40)
    assert FakeClient.options[0]["follow_redirects"] is False
    assert FakeClient.options[0]["trust_env"] is False
    assert "T" * 40 not in capsys.readouterr().out


def test_apply_rejects_weak_token_without_sending(monkeypatch):
    FakeClient.calls = []
    monkeypatch.setattr(seeder.httpx, "Client", FakeClient)
    monkeypatch.setattr(sys, "argv", ["seed_gs1_resolver_ce_fixture", "--apply"])
    monkeypatch.setenv("GS1_RESOLVER_SESSION_TOKEN", "secret")
    monkeypatch.setenv("GS1_RESOLVER_BASE_URL", "http://localhost:8080")

    with pytest.raises(SystemExit):
        seeder.main()
    assert FakeClient.calls == []


def test_apply_rejects_malformed_response_envelope(monkeypatch, capsys):
    FakeClient.calls = []
    FakeClient.response = FakeResponse(body={"entry": "wrong", "result": {"response_status": 201}})
    monkeypatch.setattr(seeder.httpx, "Client", FakeClient)
    monkeypatch.setattr(sys, "argv", ["seed_gs1_resolver_ce_fixture", "--apply"])
    monkeypatch.setenv("GS1_RESOLVER_SESSION_TOKEN", "T" * 40)
    monkeypatch.setenv("GS1_RESOLVER_BASE_URL", "http://localhost:8080")

    assert seeder.main() == 2
    assert "did not match" in capsys.readouterr().out


def test_apply_rejects_invalid_response_headers(monkeypatch, capsys):
    FakeClient.calls = []
    FakeClient.response = FakeResponse()
    FakeClient.response.headers["content-length"] = "invalid"
    monkeypatch.setattr(seeder.httpx, "Client", FakeClient)
    monkeypatch.setattr(sys, "argv", ["seed_gs1_resolver_ce_fixture", "--apply"])
    monkeypatch.setenv("GS1_RESOLVER_SESSION_TOKEN", "T" * 40)
    monkeypatch.setenv("GS1_RESOLVER_BASE_URL", "http://localhost:8080")

    assert seeder.main() == 2
    assert "invalid Content-Length" in capsys.readouterr().out
