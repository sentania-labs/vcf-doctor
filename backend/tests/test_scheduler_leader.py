"""One worker runs scheduled scans, and it is replaceable.

The advisory lock is what makes that true across workers and pods, so these
tests exercise losing it and taking it back rather than the APScheduler wiring.
"""

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


class _FakeScheduler:
    """Enough of APScheduler for the leadership logic. The suite never starts a
    real one (scheduler_enabled() is false under pytest)."""

    def __init__(self):
        self.jobs = {}

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)

    def add_job(self, func, trigger, *, id, args=None, **kw):
        job = _FakeJob(id)
        self.jobs[id] = job
        return job

    def shutdown(self, wait=False):
        self.jobs.clear()


class _FakeJob:
    def __init__(self, job_id):
        self.id = job_id
        self.next_run_time = None
