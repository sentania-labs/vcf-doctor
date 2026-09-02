import pytest


@pytest.fixture(autouse=True)
def _no_real_tcp_preflight(monkeypatch):
    """Collector tests mock SmartConnect; never open real sockets in the suite."""
    try:
        import app.collectors.vsphere.client as client
    except ImportError:
        return
    monkeypatch.setattr(client, "tcp_preflight", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _fixture_collector_allowed(monkeypatch):
    """The suite runs against the bundled fixture collector, which is gated
    behind the VCF_DOCTOR_TEST_FIXTURES hook in production."""
    from app.config import settings

    monkeypatch.setattr(settings, "test_fixtures", True)


FIXTURE_CONN = {
    "name": "Test Workload Domain",
    "host": "fixture",
    "username": "test",
    "password": "",
    "kind": "fixture",
    "interval_minutes": 15,
}


def seed_fixture_connection(client) -> str:
    """What the retired demo mode did at startup: one fixture connection,
    scanned once (snapshot A). Returns the connection id."""
    cid = client.post("/api/connections", json=FIXTURE_CONN).json()["id"]
    r = client.post("/api/scan", json={"connection_id": cid})
    assert r.status_code == 200 and r.json()[0]["status"] == "ok", r.text
    return cid


@pytest.fixture(autouse=True)
def _auth_off_by_default(monkeypatch, request):
    """Existing tests exercise features, not login. tests/test_auth.py opts back in."""
    if request.node.fspath.basename == "test_auth.py":
        return
    from app.config import settings

    monkeypatch.setattr(settings, "auth", "off")
