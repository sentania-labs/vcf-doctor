import os

import pytest

from app import db
from app.config import settings as cfg

TEST_DATABASE_URL_ENV = "VCF_DOCTOR_TEST_DATABASE_URL"
TEST_DB_PASSWORD_FILE_ENV = "VCF_DOCTOR_TEST_DB_PASSWORD_FILE"

_NO_TEST_DATABASE = f"""
The backend suite needs its own PostgreSQL database, because it drops and
rebuilds the schema between tests.

Run `make test`, which starts a disposable postgres:16 container and points the
suite at it, or set {TEST_DATABASE_URL_ENV} yourself:

    {TEST_DATABASE_URL_ENV}=postgresql://vcf_doctor@127.0.0.1:5432/vcf_doctor_test

That variable is required on purpose. Nothing here reads the deployment's
VCF_DOCTOR_DATABASE_URL, so the suite can never drop the schema of a database
that was not named as the test database.
"""


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    """Point every test at the throwaway database named in the environment."""
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        pytest.exit(_NO_TEST_DATABASE, returncode=2)
    cfg.database_url = url
    cfg.db_password_file = os.environ.get(TEST_DB_PASSWORD_FILE_ENV, "")
    try:
        db.fetchone("SELECT 1 AS ok")
    except Exception as exc:  # noqa: BLE001  a clear message beats a stack trace here
        pytest.exit(f"{_NO_TEST_DATABASE}\nConnecting to {url} failed: {exc}", returncode=2)
    db.reset_for_tests()
    yield
    db.close()


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """The volume holds only the generated encryption key file now. Give every
    test its own, so one test's key never leaks into the next."""
    monkeypatch.setattr(cfg, "data_dir", str(tmp_path))


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
