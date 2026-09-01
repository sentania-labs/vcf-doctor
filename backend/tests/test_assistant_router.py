import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.assistant import settings as assistant_settings
from app.assistant.router import router
from app.config import settings as cfg


@pytest.fixture
def client(tmp_path, monkeypatch):
    db.reset_for_tests(str(tmp_path / "assistant.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cfg, "demo_mode", False)
    app = FastAPI()
    app.include_router(router, prefix="/api/assistant")
    return TestClient(app)


def _body(question="Why is esx03 disconnected?", task="explain"):
    return {
        "task": task,
        "context": {
            "question": question,
            "findings": [
                {
                    "id": "f1",
                    "check_id": "HOST_DISCONNECTED",
                    "severity": "critical",
                    "title": "Host disconnected",
                    "summary": "esx03 is disconnected",
                    "resource_name": "esx03",
                    "resource_type": "host",
                }
            ],
            "changes": [],
            "resources": [
                {"id": "host:vc01:esx03", "type": "host", "name": "esx03", "source": "vcenter:vc01"}
            ],
        },
    }


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if name and data:
            events.append((name, json.loads(data)))
    return events


def test_status_without_key_is_unavailable_with_reason(client):
    r = client.get("/api/assistant/status")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["provider"] == "anthropic"
    assert "API key" in body["reason"]


def test_ask_without_key_emits_error_event(client):
    with client.stream("POST", "/api/assistant", json=_body()) as r:
        assert r.status_code == 200
        events = _parse_sse(r.read().decode())
    assert events[0][0] == "error"
    assert "API key" in events[0][1]["message"]


def test_sse_end_to_end_with_mock(client):
    r = client.put("/api/assistant/settings", json={"provider": "mock"})
    assert r.status_code == 200 and r.json()["provider"] == "mock"
    assert client.get("/api/assistant/status").json()["available"] is True

    with client.stream("POST", "/api/assistant", json=_body()) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(r.read().decode())

    names = [n for n, _ in events]
    assert names.count("delta") > 3
    assert names[-1] == "done"
    assert "error" not in names
    text = "".join(d["text"] for n, d in events if n == "delta")
    assert "Host disconnected" in text and "esx03" in text
    done = events[-1][1]
    assert done["stop_reason"] == "end_turn"
    assert done["evidence"] == {"findings": 1, "changes": 0, "resources": 1}


def test_disabled_assistant_reports_reason(client):
    client.put("/api/assistant/settings", json={"provider": "mock", "enabled": False})
    st = client.get("/api/assistant/status").json()
    assert st["available"] is False and "disabled" in st["reason"].lower()
    r = client.post("/api/assistant/test")
    assert r.json()["ok"] is False


def test_test_endpoint_with_mock(client):
    client.put("/api/assistant/settings", json={"provider": "mock"})
    r = client.post("/api/assistant/test")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "No live call" in r.json()["message"]


def test_demo_mode_uses_mock_without_key(client, monkeypatch):
    monkeypatch.setattr(cfg, "demo_mode", True)
    assert assistant_settings.get_provider().name == "mock"
    assert client.get("/api/assistant/status").json()["available"] is True


def test_api_error_detail_uses_body_message():
    import httpx
    from anthropic import APIStatusError

    from app.assistant.providers.anthropic_provider import _api_error_detail

    resp = httpx.Response(
        400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    body = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "fallbacks: nope"},
    }
    e = APIStatusError("400", response=resp, body=body)
    assert _api_error_detail(e) == "invalid_request_error: fallbacks: nope"
    assert "sk-" not in _api_error_detail(e)


def test_fallbacks_only_for_supporting_models():
    from app.assistant.providers.anthropic_provider import supports_fallbacks

    assert supports_fallbacks("claude-opus-5")
    assert supports_fallbacks("claude-fable-5")
    assert not supports_fallbacks("claude-sonnet-5")
    assert not supports_fallbacks("claude-haiku-4-5")
    assert not supports_fallbacks("claude-opus-4-8")
