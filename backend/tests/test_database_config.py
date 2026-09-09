"""How the database connection is configured: the URL is a deployment binding
and the password never travels in an environment variable."""

import pytest

from app import config, db
from app.config import settings as cfg


def test_password_comes_from_a_file(tmp_path, monkeypatch):
    secret = tmp_path / "vcf-doctor-db-password"
    secret.write_text("s3cret\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "db_password_file", str(secret))
    assert config.database_password() == "s3cret"


def test_no_password_file_means_no_password(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "db_password_file", str(tmp_path / "absent"))
    assert config.database_password() is None
    monkeypatch.setattr(cfg, "db_password_file", "")
    assert config.database_password() is None


def test_a_password_in_the_url_is_refused(monkeypatch):
    """No supported path carries a database password in an environment
    variable, so a URL that holds one fails loudly rather than working."""
    monkeypatch.setattr(cfg, "database_url", "postgresql://vcf_doctor:hunter2@pg:5432/vcf_doctor")
    with pytest.raises(config.PasswordInUrl) as refused:
        config.database_url_without_password()
    assert "VCF_DOCTOR_DB_PASSWORD_FILE" in str(refused.value)
    with pytest.raises(config.PasswordInUrl):
        db.conninfo()


def test_a_url_without_a_password_is_accepted(monkeypatch):
    monkeypatch.setattr(cfg, "database_url", "postgresql://vcf_doctor@pg:5432/vcf_doctor")
    assert config.database_url_without_password() == "postgresql://vcf_doctor@pg:5432/vcf_doctor"


def test_conninfo_carries_the_file_password(tmp_path, monkeypatch):
    secret = tmp_path / "pw"
    secret.write_text("from-the-file", encoding="utf-8")
    monkeypatch.setattr(cfg, "database_url", "postgresql://vcf_doctor@pg:5432/vcf_doctor")
    monkeypatch.setattr(cfg, "db_password_file", str(secret))
    assert "password=from-the-file" in db.conninfo()


def test_a_reachable_migrated_database_is_healthy():
    db.reset_for_tests()
    assert db.healthy() == (True, None)


def test_an_unreachable_database_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(cfg, "database_url", "postgresql://nobody@127.0.0.1:1/nothing")
    monkeypatch.setattr(cfg, "db_password_file", "")
    monkeypatch.setattr(cfg, "db_pool_timeout", 0.5)
    db.close()
    try:
        ok, error = db.healthy()
        assert ok is False and error
    finally:
        db.close()


def test_health_answers_while_the_database_is_down(monkeypatch):
    """A PostgreSQL failover must not crash-loop the container. The console
    keeps answering the health probe and says the database is unavailable.

    Anything that needs the database still fails while it is down, sign-in
    included. What must not happen is the probe timing out or the process
    dying, either of which takes down a pod that is only waiting for its
    database to come back.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    db.reset_for_tests()
    with TestClient(app) as client:
        assert client.get("/api/health").json()["database"] is True
        monkeypatch.setattr(cfg, "database_url", "postgresql://nobody@127.0.0.1:1/nothing")
        monkeypatch.setattr(cfg, "db_password_file", "")
        monkeypatch.setattr(cfg, "db_pool_timeout", 0.5)
        monkeypatch.setattr(db, "PROBE_TIMEOUT", 0.5)
        db.close()
        body = client.get("/api/health")
        assert body.status_code == 200
        assert body.json()["database"] is False
        assert body.json()["status"] == "ok"
    db.close()  # the next pool is built from the restored URL


def test_trusted_proxies_trust_nobody_when_the_database_is_unreadable(monkeypatch):
    """The forwarded-headers middleware reads a setting on every request. An
    unreachable database must make it trust nobody, not raise."""
    from app import proxies

    monkeypatch.setattr(cfg, "database_url", "postgresql://nobody@127.0.0.1:1/nothing")
    monkeypatch.setattr(cfg, "db_password_file", "")
    monkeypatch.setattr(cfg, "db_pool_timeout", 0.5)
    db.close()
    try:
        assert proxies.stored_value() == []
        assert proxies.networks() == []
    finally:
        db.close()
