"""How the database connection is configured: the URL is a deployment binding
and the password never travels in an environment variable."""

import base64
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
from psycopg.conninfo import conninfo_to_dict

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


@pytest.mark.parametrize(
    "connection",
    [
        "postgresql://vcf_doctor:hunter2@pg:5432/vcf_doctor",
        "host=pg dbname=vcf_doctor user=vcf_doctor password=hunter2",
    ],
)
def test_a_password_in_the_url_is_refused(monkeypatch, connection):
    """No supported path carries a database password in an environment
    variable, so a URL that holds one fails loudly rather than working."""
    monkeypatch.setattr(cfg, "database_url", connection)
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
    parsed = conninfo_to_dict(db.conninfo())
    assert parsed["password"] == "from-the-file"
    expected = {
        "connect_timeout": "5",
        "tcp_user_timeout": "60000",
        "keepalives": "1",
        "keepalives_idle": "20",
        "keepalives_interval": "10",
        "keepalives_count": "3",
    }
    assert {key: parsed[key] for key in expected} == expected


def test_conninfo_preserves_explicit_socket_timeouts(monkeypatch):
    monkeypatch.setattr(
        cfg,
        "database_url",
        "postgresql://vcf_doctor@pg:5432/vcf_doctor?connect_timeout=9&"
        "tcp_user_timeout=90000&keepalives=0&keepalives_idle=30&"
        "keepalives_interval=15&keepalives_count=4",
    )
    monkeypatch.setattr(cfg, "db_password_file", "")
    parsed = conninfo_to_dict(db.conninfo())
    assert parsed["connect_timeout"] == "9"
    assert parsed["tcp_user_timeout"] == "90000"
    assert parsed["keepalives"] == "0"
    assert parsed["keepalives_idle"] == "30"
    assert parsed["keepalives_interval"] == "15"
    assert parsed["keepalives_count"] == "4"


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


def _unreachable(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "database_url", "postgresql://nobody@127.0.0.1:1/nothing")
    monkeypatch.setattr(cfg, "db_password_file", "")
    monkeypatch.setattr(cfg, "db_pool_timeout", 0.5)
    monkeypatch.setattr(db, "PROBE_TIMEOUT", 0.5)
    db.close()


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _http_json(url: str) -> tuple[int, dict]:
    try:
        response = urllib.request.urlopen(url, timeout=2)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())
    with response:
        return response.status, json.loads(response.read())


def test_cold_start_serves_liveness_then_recovers_readiness(monkeypatch, caplog):
    import uvicorn

    from app import scheduler
    from app.main import app

    db.reset_for_tests()
    original_url = cfg.database_url
    original_password_file = cfg.db_password_file
    scheduler.shutdown()
    monkeypatch.setattr(scheduler, "_background_jobs_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "RECONCILE_SECONDS", 0.05)
    monkeypatch.setattr(scheduler, "STARTUP_RETRY_SECONDS", 2)
    _unreachable(monkeypatch)
    monkeypatch.setattr(cfg, "db_pool_timeout", 0.1)
    monkeypatch.setattr(db, "PROBE_TIMEOUT", 0.1)
    caplog.set_level(logging.INFO, logger="vcf_doctor.scan")

    port = _free_port()
    server_config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        lifespan="on",
        loop="asyncio",
        http="h11",
        ws="none",
        log_config=None,
        access_log=False,
    )
    server_config.load()
    server = uvicorn.Server(server_config)
    thread = threading.Thread(target=server.run, daemon=True)
    started = time.monotonic()
    thread.start()
    deadline = started + 2
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        assert server.started
        assert time.monotonic() - started < 0.75
        base = f"http://127.0.0.1:{port}"
        probe_started = time.monotonic()
        status, live = _http_json(f"{base}/api/health/live")
        assert status == 200 and live["status"] == "ok"
        assert time.monotonic() - probe_started < 0.3

        status, ready = _http_json(f"{base}/api/health/ready")
        assert status == 503
        assert ready["status"] == "degraded" and ready["database"] is False
        assert ready["startup_failures"] == []

        deadline = time.monotonic() + 10
        while (
            "deferred startup work is waiting for the database" not in caplog.text
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert "deferred startup work is waiting for the database" in caplog.text

        monkeypatch.setattr(cfg, "database_url", original_url)
        monkeypatch.setattr(cfg, "db_password_file", original_password_file)
        db.close()
        status, ready = _http_json(f"{base}/api/health/ready")
        assert status == 200
        assert ready["status"] == "ok"
        assert ready["database"] is True
        assert ready["startup_complete"] is False
        assert ready["startup_failures"] == []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, ready = _http_json(f"{base}/api/health/ready")
            if ready["startup_complete"] is True:
                break
            time.sleep(0.05)
        assert status == 200
        assert ready["status"] == "ok" and ready["database"] is True
        assert ready["startup_complete"] is True
        assert ready["startup_failures"] == []
        assert set(ready) == {
            "status",
            "version",
            "scheduler",
            "database",
            "startup_complete",
            "startup_failures",
        }
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        scheduler.shutdown()
        db.close()


def test_authentication_database_wait_does_not_delay_liveness(monkeypatch):
    from fastapi.testclient import TestClient

    from app import auth
    from app.main import app

    db.reset_for_tests()
    monkeypatch.setattr(cfg, "auth", "on")
    _unreachable(monkeypatch)
    read_started = threading.Event()
    original_get_setting = db.get_setting

    def tracked_get_setting(key, *args, **kwargs):
        if key == auth._SECRET_KEY:
            read_started.set()
        return original_get_setting(key, *args, **kwargs)

    monkeypatch.setattr(db, "get_setting", tracked_get_setting)
    token = base64.urlsafe_b64encode(b"1" + (b"x" * 32)).decode()
    responses = []
    with TestClient(app) as blocked_client, TestClient(app) as live_client:
        blocked_client.cookies.set(auth.COOKIE, token)
        thread = threading.Thread(
            target=lambda: responses.append(blocked_client.get("/api/scans"))
        )
        thread.start()
        assert read_started.wait(timeout=1)
        started = time.monotonic()
        live = live_client.get("/api/health/live")
        elapsed = time.monotonic() - started
        thread.join(timeout=2)
    assert live.status_code == 200
    assert elapsed < 0.3
    assert not thread.is_alive() and responses
    db.close()


def test_liveness_stays_green_while_the_database_is_down(monkeypatch):
    """Liveness is "is this process alive". Restarting a console whose database
    is down fixes nothing and a restart loop makes the outage worse, so this
    answer must not depend on the database at all."""
    from fastapi.testclient import TestClient

    from app.main import app

    db.reset_for_tests()
    with TestClient(app) as client:
        assert client.get("/api/health/live").status_code == 200
        _unreachable(monkeypatch)
        live = client.get("/api/health/live")
        assert live.status_code == 200
        assert live.json()["status"] == "ok"
        # /api/health is the older name for the same question. The deployment
        # manifest lives elsewhere and probes it, so it must not go red on the
        # database and restart-loop a pod that only needs its database back.
        old_name = client.get("/api/health")
        assert old_name.status_code == 200
        assert old_name.json() == live.json()
    db.close()  # the next pool is built from the restored URL


def test_liveness_answers_fast_from_the_first_probe_after_the_database_dies(monkeypatch):
    """A Kubernetes livenessProbe times out after one second by default, so
    liveness that merely returns 200 is not enough: it has to return quickly on
    every request, the first one after the database goes away included. Nothing
    on this path may wait on the database, handler or middleware."""
    from fastapi.testclient import TestClient

    from app import proxies
    from app.main import app

    db.reset_for_tests()
    proxies.set_stored(["10.0.0.0/8"])
    with TestClient(app) as client:
        assert client.get("/api/health/live").status_code == 200
        _unreachable(monkeypatch)
        proxies.reset_cache()  # nothing remembered, exactly as after a restart
        for path in ("/api/health", "/api/health/live"):
            for _ in range(3):
                started = time.monotonic()
                probe = client.get(path)
                elapsed = time.monotonic() - started
                assert probe.status_code == 200, path
                assert elapsed < 0.3, f"{path} took {elapsed:.2f}s"
    db.close()
    proxies.reset_cache()


def test_readiness_goes_red_while_the_database_is_down(monkeypatch):
    """Readiness is "can this instance serve". Sign-in and every page behind it
    need the database, so an instance that cannot reach it must be taken out of
    rotation rather than sent visitors it will fail."""
    from fastapi.testclient import TestClient

    from app.main import app

    db.reset_for_tests()
    with TestClient(app) as client:
        ready = client.get("/api/health/ready")
        assert ready.status_code == 200
        assert ready.json()["database"] is True

        _unreachable(monkeypatch)
        body = client.get("/api/health/ready")
        assert body.status_code == 503
        assert body.json()["status"] == "degraded"
        assert body.json()["database"] is False
        # Readiness needs no session, and the driver's diagnostic names hosts
        # and the configured secret path, so it is logged and never published.
        assert "detail" not in body.json()
    db.close()


def test_readiness_is_red_while_a_migration_is_pending(tmp_path, monkeypatch, caplog):
    """A reachable server missing its tables is not somewhere to send traffic."""
    import logging

    from fastapi.testclient import TestClient

    from app import migrate
    from app.main import app

    db.reset_for_tests()
    with TestClient(app) as client:
        assert client.get("/api/health/ready").status_code == 200
        for path in migrate.revisions():
            (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        (tmp_path / "0002_example.sql").write_text(
            "CREATE TABLE example_later (id TEXT PRIMARY KEY);", encoding="utf-8"
        )
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)
        with caplog.at_level(logging.WARNING, logger="vcf_doctor"):
            body = client.get("/api/health/ready")
        assert body.status_code == 503
        assert body.json()["database"] is False
        assert "detail" not in body.json()
        # The reason an operator needs is in the log, not in a body anyone can read.
        assert "0002_example" in caplog.text
        # Liveness is unaffected: the process is fine, its schema is not.
        assert client.get("/api/health/live").status_code == 200


def test_readiness_reports_deferred_startup_without_gating_traffic(monkeypatch, caplog):
    import logging

    from fastapi.testclient import TestClient

    from app import main, scheduler

    db.reset_for_tests()
    startup = [(False, ())]
    monkeypatch.setattr(scheduler, "startup_status", lambda: startup[0])
    monkeypatch.setattr(main, "_readiness_startup_state", None)
    with TestClient(main.app) as client:
        with caplog.at_level(logging.INFO, logger="vcf_doctor"):
            body = client.get("/api/health/ready")
            client.get("/api/health/ready")
            assert body.status_code == 200
            assert body.json()["status"] == "ok"
            assert body.json()["database"] is True
            assert body.json()["startup_complete"] is False
            assert body.json()["startup_failures"] == []
            assert set(body.json()) == {
                "status",
                "version",
                "scheduler",
                "database",
                "startup_complete",
                "startup_failures",
            }
            assert caplog.messages.count("readiness: deferred startup work is incomplete") == 1

            startup[0] = (False, ("vault_rekey",))
            client.get("/api/health/ready")
            client.get("/api/health/ready")
            assert sum(
                "deferred startup steps are failing" in msg for msg in caplog.messages
            ) == 1

            startup[0] = (True, ())
            client.get("/api/health/ready")
            client.get("/api/health/ready")
            assert caplog.messages.count("readiness: deferred startup work completed") == 1


def test_trusted_proxies_trust_nobody_when_the_database_is_unreadable(monkeypatch):
    """The forwarded-headers middleware reads a setting on every request. An
    unreachable database must make it trust nobody, not raise."""
    from app import proxies

    monkeypatch.setattr(cfg, "database_url", "postgresql://nobody@127.0.0.1:1/nothing")
    monkeypatch.setattr(cfg, "db_password_file", "")
    monkeypatch.setattr(cfg, "db_pool_timeout", 0.5)
    db.close()
    proxies.reset_cache()
    try:
        assert proxies.stored_value() == []
        assert proxies.networks() == []
    finally:
        db.close()
        proxies.reset_cache()


def test_the_stored_list_comes_back_after_the_database_does(monkeypatch):
    """The lookup answers from memory and refreshes behind the request, so an
    outage costs no waiting. Trusting nobody while the database is away is the
    safe answer, but it must not be the permanent one: the saved list has to
    return on its own once the database does."""
    from app import proxies

    db.reset_for_tests()
    proxies.set_stored(["10.42.0.0/16"])
    monkeypatch.setattr(proxies, "CACHE_TTL", 0.0)  # every read refreshes

    monkeypatch.setattr(cfg, "database_url", "postgresql://nobody@127.0.0.1:1/nothing")
    monkeypatch.setattr(cfg, "db_password_file", "")
    monkeypatch.setattr(cfg, "db_pool_timeout", 0.5)
    db.close()
    assert _settles_on(proxies, []) == []

    monkeypatch.undo()
    monkeypatch.setattr(proxies, "CACHE_TTL", 0.0)  # undo restored it; keep refreshing
    db.close()
    assert _settles_on(proxies, ["10.42.0.0/16"]) == ["10.42.0.0/16"]
    proxies.reset_cache()


def _settles_on(proxies, expected: list[str], timeout: float = 5.0) -> list[str]:
    """Poll the lookup until the background refresh has landed."""
    deadline = time.monotonic() + timeout
    value = proxies.stored_value()
    while value != expected and time.monotonic() < deadline:
        time.sleep(0.05)
        value = proxies.stored_value()
    return value


def test_a_saved_list_applies_without_waiting_for_a_refresh(monkeypatch):
    """An operator who saves Settings must not have to wait out the TTL, nor
    briefly see the list empty while a background read catches up."""
    from app import proxies

    db.reset_for_tests()
    proxies.set_stored(["10.42.0.0/16"])
    assert proxies.stored_value() == ["10.42.0.0/16"]
    proxies.set_stored(["192.0.2.0/24"])
    assert proxies.stored_value() == ["192.0.2.0/24"]
    proxies.reset_cache()
