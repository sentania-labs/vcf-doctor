"""One worker runs scheduled scans, and it is replaceable.

The advisory lock is what makes that true across workers and pods, so these
tests exercise losing it and taking it back rather than the APScheduler wiring.
"""

import psycopg
import pytest

from app import db, scheduler
from app.models import ConnectionCreate
from app.snapshots import store


@pytest.fixture(autouse=True)
def _fresh_db():
    db.reset_for_tests()
    yield
    scheduler.shutdown()


def _conn(**kw):
    fields = {"name": "c", "host": "fixture", "username": "u", "password": "p", "kind": "fixture"}
    fields.update(kw)
    return store.create_connection(ConnectionCreate(**fields))


def test_only_one_holder_at_a_time():
    assert db.acquire_scheduler_lock() is True
    assert db.scheduler_lock_held() is True
    assert db.scheduler_lock_alive() is True
    db.release_scheduler_lock()
    assert db.scheduler_lock_held() is False
    assert db.scheduler_lock_alive() is False


def test_a_second_session_is_refused_while_the_lock_is_held():
    """Stands in for a second worker or pod: the lock is one per deployment."""
    assert db.acquire_scheduler_lock() is True
    with db.pool().connection() as other:
        taken = other.execute(
            "SELECT pg_try_advisory_lock(%s, %s) AS ok",
            (db.LOCK_CLASS_SCHEDULER, db.LOCK_OBJ_SCHEDULER),
        ).fetchone()["ok"]
    assert taken is False
    db.release_scheduler_lock()


def test_running_is_true_from_a_worker_that_is_not_the_leader():
    """/api/health and Settings must not depend on which worker answered."""
    assert scheduler.running() is False
    assert db.acquire_scheduler_lock() is True
    assert scheduler._leader is False  # this process took the lock, not the scheduler
    assert scheduler.running() is True
    db.release_scheduler_lock()
    assert scheduler.running() is False


def test_running_is_false_when_the_database_cannot_be_asked(monkeypatch):
    """A leader whose database is unreachable is not scheduling anything, and
    must not report that it is next to a readiness answer saying so."""
    from app.config import settings as cfg

    assert db.acquire_scheduler_lock() is True
    assert scheduler.running() is True
    monkeypatch.setattr(cfg, "database_url", "postgresql://nobody@127.0.0.1:1/nothing")
    monkeypatch.setattr(cfg, "db_password_file", "")
    monkeypatch.setattr(cfg, "db_pool_timeout", 0.5)
    monkeypatch.setattr(db, "PROBE_TIMEOUT", 0.5)
    db.close()
    assert scheduler.running() is False
    db.close()


def test_leadership_is_retaken_after_the_lock_session_ends(monkeypatch):
    """A restarted or failed-over database ends the lock session. The scheduler
    has to notice and take the lock again, or scheduled scans stop silently."""
    _conn()
    monkeypatch.setattr(scheduler, "_scheduler", _FakeScheduler())
    scheduler.take_leadership()
    assert scheduler._leader is True
    assert set(scheduler._scheduled_state) == {c.id for c in store.list_connections()}

    # What a PostgreSQL restart does to the session holding the lock.
    db._leader.close()
    assert db.scheduler_lock_alive() is False

    scheduler.take_leadership()
    assert scheduler._leader is True
    assert db.scheduler_lock_alive() is True
    assert set(scheduler._scheduled_state) == {c.id for c in store.list_connections()}


def test_a_schedule_saved_on_another_worker_is_picked_up(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(scheduler, "_scheduler", _FakeScheduler())
    scheduler.take_leadership()
    assert scheduler._scheduled_state[conn.id][1] is True

    # Another worker serves the request and only writes the row.
    store.update_schedule(conn.id, interval_minutes=45, enabled=False)
    scheduler.reconcile_jobs()
    assert scheduler._scheduled_state[conn.id] == (45, False)

    # And a connection deleted elsewhere loses its job.
    store.delete_connection(conn.id)
    scheduler.reconcile_jobs()
    assert conn.id not in scheduler._scheduled_state


def test_leader_revisits_an_interrupted_run_after_a_live_scan_finishes(monkeypatch):
    conn = _conn()
    run = store.create_run(conn.id, "manual")
    monkeypatch.setattr(scheduler, "_scheduler", _FakeScheduler())

    with db.try_advisory_lock(db.SCAN_LOCK, conn.id) as held:
        assert held is True
        scheduler.take_leadership()
        assert store.get_run(run.id).status == "running"

    scheduler.take_leadership()
    assert store.get_run(run.id).status == "error"
    scheduler.take_leadership()
    assert store.get_run(run.id).status == "error"


def test_startup_failures_are_isolated_and_do_not_stop_scheduled_scans(monkeypatch):
    from fastapi.testclient import TestClient

    from app import auth, vault
    from app.events import store as events_store
    from app.main import app

    failed_retention = _conn(name="retention-fails")
    blocked_retention = _conn(name="retention-blocked")
    retained = _conn(name="retention-succeeds")
    calls: dict[str, int] = {}
    retention_calls: dict[str, int] = {}

    def record(name):
        def run():
            calls[name] = calls.get(name, 0) + 1

        return run

    def fail_rekey():
        calls["vault_rekey"] = calls.get("vault_rekey", 0) + 1
        raise RuntimeError("key volume is read-only")

    def backfill():
        calls["change_log_backfill"] = calls.get("change_log_backfill", 0) + 1
        return {}

    def reconcile():
        calls["scan_reconciliation"] = calls.get("scan_reconciliation", 0) + 1
        raise RuntimeError("scan reconciliation failed")

    def apply_retention(connection_id, _policy=None):
        retention_calls[connection_id] = retention_calls.get(connection_id, 0) + 1
        if connection_id == failed_retention.id:
            raise RuntimeError("retention failed")
        if connection_id == blocked_retention.id:
            raise psycopg.OperationalError("connection lost")

    monkeypatch.setattr(vault, "rekey_at_startup", fail_rekey)
    monkeypatch.setattr(vault, "migrate_plaintext", record("vault_plaintext"))
    monkeypatch.setattr(events_store, "seed_defaults", record("event_defaults"))
    monkeypatch.setattr(store, "backfill_log_since", backfill)
    monkeypatch.setattr(store, "reconcile_interrupted_runs", reconcile)
    monkeypatch.setattr(auth, "bootstrap_from_env", record("auth_bootstrap"))
    monkeypatch.setattr(
        scheduler, "disable_stale_fixture_schedules", record("fixture_schedules")
    )
    monkeypatch.setattr(store, "apply_retention", apply_retention)
    fake_scheduler = _FakeScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)
    scheduler._begin_startup()

    scheduler._maintenance_job()
    scheduler._maintenance_job()

    assert scheduler.startup_status() == (
        False,
        ("retention", "scan_reconciliation", "vault_rekey"),
    )
    assert calls["vault_rekey"] == 2
    assert calls["scan_reconciliation"] == 4
    for name in (
        "vault_plaintext",
        "event_defaults",
        "change_log_backfill",
        "auth_bootstrap",
        "fixture_schedules",
    ):
        assert calls[name] == 1
    assert retention_calls == {
        failed_retention.id: 2,
        blocked_retention.id: 2,
        retained.id: 1,
    }
    assert fake_scheduler.maintenance_intervals == [5.0, 5.0]
    assert scheduler._leader is True
    assert set(scheduler._scheduled_state) == {
        failed_retention.id,
        blocked_retention.id,
        retained.id,
    }

    with TestClient(app) as client:
        ready = client.get("/api/health/ready")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ok"
        assert ready.json()["database"] is True
        assert "key volume is read-only" not in ready.text
        assert ready.json()["scheduler"] is True
        assert client.get("/api/health/live").status_code == 200
        scan = client.post("/api/scan", json={"connection_id": retained.id})
        assert scan.status_code == 200
        assert scan.json()[0]["status"] == "ok"


def test_startup_names_server_errors_but_not_connection_failures(monkeypatch):
    from app import vault

    def connection_lost():
        raise psycopg.OperationalError("connection lost")

    def disk_full():
        raise psycopg.errors.DiskFull("disk full")

    monkeypatch.setattr(vault, "rekey_at_startup", connection_lost)
    monkeypatch.setattr(vault, "migrate_plaintext", disk_full)
    monkeypatch.setattr(scheduler, "scheduler_enabled", lambda: False)
    fake_scheduler = _FakeScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)
    scheduler._begin_startup()

    scheduler._maintenance_job()

    assert scheduler.startup_status() == (False, ("vault_plaintext",))
    assert fake_scheduler.maintenance_intervals == [5.0]


def test_pass_level_failure_is_logged_without_a_public_step(monkeypatch):
    def pass_failure():
        raise RuntimeError("pass failed")

    monkeypatch.setattr(scheduler, "startup_maintenance", pass_failure)
    monkeypatch.setattr(scheduler, "scheduler_enabled", lambda: False)
    fake_scheduler = _FakeScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)
    scheduler._begin_startup()

    scheduler._maintenance_job()

    assert scheduler.startup_status() == (False, ())
    assert fake_scheduler.maintenance_intervals == [60.0]


def test_leadership_classifies_database_waits_without_hiding_failures(monkeypatch, caplog):
    def unavailable():
        raise psycopg.OperationalError("connection lost")

    monkeypatch.setattr(scheduler, "take_leadership", unavailable)
    caplog.set_level("INFO", logger="vcf_doctor.scan")

    scheduler._leadership_job()

    assert "scheduler leadership is waiting for the database" in caplog.messages
    assert not any(record.levelname == "ERROR" for record in caplog.records)

    caplog.clear()

    def failure():
        raise RuntimeError("leadership failed")

    monkeypatch.setattr(scheduler, "take_leadership", failure)
    scheduler._leadership_job()

    assert "scheduler leadership check failed; retrying next interval" in caplog.messages
    assert any(record.levelname == "ERROR" for record in caplog.records)


class _FakeScheduler:
    """Enough of APScheduler for the leadership logic. The suite never starts a
    real one (_background_jobs_enabled() is false under pytest)."""

    def __init__(self):
        self.jobs = {}
        self.maintenance_intervals = []

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)

    def add_job(self, func, trigger, *, id, args=None, **kw):
        job = _FakeJob(id)
        self.jobs[id] = job
        return job

    def reschedule_job(self, job_id, *, trigger):
        self.maintenance_intervals.append(trigger.interval.total_seconds())
        return self.jobs.get(job_id)

    def shutdown(self, wait=False):
        self.jobs.clear()


class _FakeJob:
    def __init__(self, job_id):
        self.id = job_id
        self.next_run_time = None
