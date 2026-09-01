import pytest


@pytest.fixture(autouse=True)
def _no_real_tcp_preflight(monkeypatch):
    """Collector tests mock SmartConnect; never open real sockets in the suite."""
    try:
        import app.collectors.vsphere.client as client
    except ImportError:
        return
    monkeypatch.setattr(client, "tcp_preflight", lambda *a, **k: None)
