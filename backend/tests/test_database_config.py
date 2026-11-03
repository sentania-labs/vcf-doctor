"""How the database connection is configured: the URL is a deployment binding
and the password never travels in an environment variable."""

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
        status, ready = _http_json(f"{base}/api/health/ready")
        assert status == 200
        assert ready["status"] == "ok" and ready["database"] is True
        assert set(ready) == {
            "status",
            "version",
            "scheduler",
            "database",
        }
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        scheduler.shutdown()
        db.close()


def test_saturated_proxy_database_wait_does_not_delay_liveness(monkeypatch):
    from fastapi.testclient import TestClient

    from app import proxies
    from app.main import app

    db.reset_for_tests()
    blocked_requests = 40
    reads_started = 0
    reads_guard = threading.Lock()
    all_reads_started = threading.Event()
    release_reads = threading.Event()
    original_get_setting = db.get_setting

    def delayed_get_setting(key, *args, **kwargs):
        nonlocal reads_started
        if key == proxies.SETTING_KEY:
            with reads_guard:
                reads_started += 1
                if reads_started >= blocked_requests:
                    all_reads_started.set()
            assert release_reads.wait(timeout=5)
            return []
        return original_get_setting(key, *args, **kwargs)

    monkeypatch.setattr(db, "get_setting", delayed_get_setting)
    responses = []
    threads = []
    release_timer = threading.Timer(1, release_reads.set)
    with TestClient(app) as client:
        try:
            for _ in range(blocked_requests):
                thread = threading.Thread(
                    target=lambda: responses.append(client.get("/api/version"))
                )
                threads.append(thread)
                thread.start()
            assert all_reads_started.wait(timeout=3)
            release_timer.start()
            started = time.monotonic()
            live = client.get("/api/health/live")
            elapsed = time.monotonic() - started
        finally:
            release_reads.set()
            release_timer.cancel()
            for thread in threads:
                thread.join(timeout=5)
    assert live.status_code == 200
    assert elapsed < 0.3
    assert all(not thread.is_alive() for thread in threads)
    assert len(responses) == blocked_requests
    assert all(response.status_code == 200 for response in responses)
    db.close()
    proxies.reset_cache()


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


def test_auth_status_answers_while_the_database_is_down(monkeypatch):
    """The login page and the console shell ask /api/auth/status first. With the
    database gone it must say so, quickly, rather than 500 and leave the shell
    blaming a backend that is in fact serving (issue 75)."""
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    db.reset_for_tests()
    with TestClient(app) as client:
        _unreachable(monkeypatch)
        for enabled, authenticated in (("on", False), ("off", True)):
            monkeypatch.setattr(settings, "auth", enabled)
            started = time.monotonic()
            reply = client.get("/api/auth/status")
            assert time.monotonic() - started < 2.0
            assert reply.status_code == 200
            assert reply.json() == {
                "enabled": enabled == "on",
                "authenticated": authenticated,
                "database": "unavailable",
                "reason": "unreachable",
            }
        # Nothing that needs the database is pretended to work.
        monkeypatch.setattr(settings, "auth", "on")
        assert client.get("/api/connections").status_code != 200
    db.close()


def test_a_signed_in_request_is_refused_quickly_while_the_database_is_down(monkeypatch):
    """A browser holding a session cookie, or a script holding an API token,
    must not wait out the pool timeout (the settings page sat on skeletons for
    ten seconds), and must be told the database is the reason (503), not that
    its credential is bad (401). Signing out must still clear the cookie and
    succeed."""
    from fastapi.testclient import TestClient

    from app import auth
    from app.config import settings
    from app.main import app

    db.reset_for_tests()
    monkeypatch.setattr(settings, "auth", "on")
    with TestClient(app) as client:
        cookie = auth.issue_token()
        client.cookies.set(auth.COOKIE, cookie)
        _unreachable(monkeypatch)
        monkeypatch.setattr(cfg, "db_pool_timeout", 10.0)  # the real default, longer than the probe
        for path in ("/api/settings", "/api/connections"):
            started = time.monotonic()
            reply = client.get(path)
            assert time.monotonic() - started < 3.0, path
            assert reply.status_code == 503, path
            assert reply.json() == {
                "enabled": True,
                "authenticated": False,
                "database": "unavailable",
                "reason": "unreachable",
            }, path
        started = time.monotonic()
        scripted = client.get(
            "/api/connections", headers={"Authorization": "Bearer vcfd_pat_whatever"}
        )
        assert time.monotonic() - started < 3.0
        assert scripted.status_code == 503
        assert scripted.json()["database"] == "unavailable"
        started = time.monotonic()
        out = client.post("/api/auth/logout")
        assert time.monotonic() - started < 3.0
        assert out.status_code == 200
        assert auth.COOKIE in out.headers["set-cookie"] and "Max-Age=0" in out.headers["set-cookie"]
    db.close()


def test_a_database_that_turns_the_console_away_is_an_outage_with_its_own_reason(monkeypatch):
    """The server is up and answering, but the database the console asks for is
    not there: nothing will ever be served, so it is reported at once, with the
    cause, instead of being waited on as a busy pool."""
    from fastapi.testclient import TestClient
    from psycopg.conninfo import make_conninfo

    from app import auth
    from app.config import settings
    from app.main import app

    db.reset_for_tests()
    monkeypatch.setattr(settings, "auth", "on")
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE, auth.issue_token())
        wrong = make_conninfo(config.database_url_without_password(), dbname="no_such_database")
        monkeypatch.setattr(cfg, "database_url", wrong)
        monkeypatch.setattr(cfg, "db_pool_timeout", 10.0)
        monkeypatch.setattr(db, "PROBE_TIMEOUT", 0.5)
        db.close()
        started = time.monotonic()
        status = client.get("/api/auth/status")
        gated = client.get("/api/settings")
        assert time.monotonic() - started < 5.0
        assert status.status_code == 200
        assert status.json()["database"] == "unavailable"
        assert status.json()["reason"] == "database"
        assert gated.status_code == 503
        assert gated.json()["reason"] == "database"
    db.close()


def test_a_refusal_is_told_from_an_outage_by_the_servers_message():
    import psycopg

    wrong_password = psycopg.OperationalError(
        'connection failed: connection to server at "10.0.0.5", port 5432 failed: '
        'FATAL:  password authentication failed for user "vcf_doctor"'
    )
    no_database = psycopg.OperationalError(
        'connection failed: connection to server at "10.0.0.5", port 5432 failed: '
        'FATAL:  database "nope" does not exist'
    )
    no_role = psycopg.OperationalError('FATAL:  role "ghost" does not exist')
    refused = psycopg.OperationalError(
        'connection failed: connection to server at "127.0.0.1", port 1 failed: Connection refused'
    )
    assert db.outage_reason(wrong_password) == "credentials"
    assert db.outage_reason(no_database) == "database"
    assert db.outage_reason(no_role) == "unreachable"
    assert db.outage_reason(refused) == "unreachable"
    assert db.outage_reason(ValueError("not a database problem")) is None


def test_a_busy_pool_is_not_an_outage(monkeypatch):
    """Every pooled connection held for longer than the probe wait, with the
    server up: sign-in state and signed-in requests wait their turn and are
    answered normally, instead of reporting the database as unavailable."""
    from fastapi.testclient import TestClient

    from app import auth
    from app.config import settings
    from app.main import app

    db.reset_for_tests()
    monkeypatch.setattr(settings, "auth", "on")
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE, auth.issue_token())
        db.close()
        monkeypatch.setattr(cfg, "db_pool_max_size", 1)
        monkeypatch.setattr(cfg, "db_pool_timeout", 10.0)
        monkeypatch.setattr(db, "PROBE_TIMEOUT", 0.2)
        db.pool()

        def saturated_for(seconds: float) -> threading.Thread:
            held = threading.Event()

            def hold() -> None:
                with db.pool().connection():
                    held.set()
                    time.sleep(seconds)

            holder = threading.Thread(target=hold, daemon=True)
            holder.start()
            assert held.wait(5)
            return holder

        holder = saturated_for(0.6)
        status = client.get("/api/auth/status")
        holder.join(5)
        assert status.status_code == 200
        assert "database" not in status.json()

        holder = saturated_for(0.6)
        signed_in = client.get("/api/settings")
        holder.join(5)
        assert signed_in.status_code == 200
    db.close()


def test_auth_status_answers_while_the_database_is_not_migrated(monkeypatch):
    """A reachable server with no tables is as unusable as a missing one, and
    the shell must still be able to open Settings."""
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    db.reset_for_tests()
    monkeypatch.setattr(settings, "auth", "on")
    with TestClient(app) as client:
        with db.transaction() as c:
            c.execute("ALTER TABLE settings RENAME TO settings_away")
        try:
            reply = client.get("/api/auth/status")
            assert reply.status_code == 200
            body = reply.json()
            assert body["database"] == "unavailable" and body["authenticated"] is False
            assert body["reason"] == "unmigrated"
            assert body["enabled"] is True and "configured" not in body
        finally:
            with db.transaction() as c:
                c.execute("ALTER TABLE settings_away RENAME TO settings")
    db.close()


def test_auth_status_has_no_database_field_when_it_is_up(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    db.reset_for_tests()
    monkeypatch.setattr(settings, "auth", "on")
    with TestClient(app) as client:
        body = client.get("/api/auth/status").json()
    assert "database" not in body and body["configured"] is False
    db.close()


def test_readiness_logs_each_database_state_once(monkeypatch, caplog):
    """Readiness is "can this instance serve". Sign-in and every page behind it
    need the database, so an instance that cannot reach it must be taken out of
    rotation rather than sent visitors it will fail."""
    from fastapi.testclient import TestClient

    from app import main

    db.reset_for_tests()
    original_url = cfg.database_url
    original_password_file = cfg.db_password_file
    original_healthy = main.db.healthy
    monkeypatch.setattr(main, "_readiness_database_state", None)
    with TestClient(main.app) as client:
        with caplog.at_level(logging.INFO, logger="vcf_doctor"):
            ready = client.get("/api/health/ready")
            assert ready.status_code == 200
            assert ready.json()["database"] is True

            _unreachable(monkeypatch)
            body = client.get("/api/health/ready")
            client.get("/api/health/ready")
            assert body.status_code == 503
            assert body.json()["status"] == "degraded"
            assert body.json()["database"] is False
            assert "detail" not in body.json()
            assert sum(
                "readiness: the database is not usable" in message
                for message in caplog.messages
            ) == 1

            monkeypatch.setattr(
                main.db,
                "healthy",
                lambda: (False, "pending migration(s): 0001_initial"),
            )
            client.get("/api/health/ready")
            client.get("/api/health/ready")
            assert caplog.messages.count(
                "readiness: the database is not usable: pending migration(s): 0001_initial"
            ) == 1

            monkeypatch.setattr(main.db, "healthy", original_healthy)
            monkeypatch.setattr(cfg, "database_url", original_url)
            monkeypatch.setattr(cfg, "db_password_file", original_password_file)
            db.close()
            client.get("/api/health/ready")
            client.get("/api/health/ready")
            assert caplog.messages.count("readiness: the database is usable again") == 1
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
    have an older background read replace the saved list when it finishes."""
    from app import proxies

    db.reset_for_tests()
    proxies.set_stored(["10.42.0.0/16"])
    proxies.reset_cache()
    read_started = threading.Event()
    release_read = threading.Event()
    threads: list[threading.Thread] = []
    real_thread = threading.Thread

    def stale_read(*args, **kwargs):
        read_started.set()
        assert release_read.wait(2)
        return ["10.42.0.0/16"]

    def record_thread(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(db, "get_setting", stale_read)
    monkeypatch.setattr(proxies.threading, "Thread", record_thread)
    assert proxies.stored_value() == []
    assert read_started.wait(2)
    proxies.set_stored(["192.0.2.0/24"])
    release_read.set()
    threads[0].join(2)
    assert not threads[0].is_alive()
    assert proxies.stored_value() == ["192.0.2.0/24"]
    proxies.reset_cache()
