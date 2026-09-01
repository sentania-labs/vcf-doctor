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
def _auth_off_by_default(monkeypatch, request):
    """Existing tests exercise features, not login. tests/test_auth.py opts back in."""
    if request.node.fspath.basename == "test_auth.py":
        return
    from app.config import settings

    monkeypatch.setattr(settings, "auth", "off")
