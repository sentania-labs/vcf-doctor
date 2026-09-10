"""Scan pipeline and APScheduler wiring.

run_scan() is the single code path for scheduled and manual captures. One scan
of a connection at a time is enforced by a PostgreSQL advisory lock, so a
manual scan on one worker and a scheduled scan on another cannot overlap.

Every worker serves the API, but only one runs scheduled scans: the one holding
the scheduler advisory lock. Every worker runs a background job that tries to
take that lock, checks it is still held, and re-reads the stored schedules. It
runs every five seconds while startup work is blocked by the database, and once
a minute otherwise. That one loop covers three things: a schedule edited on
another worker reaches the worker that owns the jobs, a database restart that
ended the lock session is noticed and the lock retaken, and a leader that goes
away is replaced by another worker or pod within a minute.
"""

import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from app import db
from app.collectors.registry import get_collector
from app.config import settings
from app.models import Finding, Resource, ScanRun, Snapshot
from app.models.snapshot import RetentionPolicy
from app.snapshots import store

log = logging.getLogger("vcf_doctor.scan")

_scheduler = None
# Whether this process currently holds the scheduler advisory lock.
_leader = False
# What the leader has jobs for: connection id -> (interval_minutes, enabled).
# Compared against the stored schedules by reconcile_jobs().
_scheduled_state: dict[str, tuple[int, bool]] = {}
_startup_pending: frozenset[str] = frozenset()
_startup_failures: tuple[str, ...] = ()
_retention_completed: set[str] = set()
RECONCILE_SECONDS = 60
STARTUP_RETRY_SECONDS = 5


class _StartupResult(NamedTuple):
    failed: bool = False
    blocked: bool = False


def retention_policy() -> RetentionPolicy:
    """Effective tier policy (settings KV `retention_policy`, else env defaults)."""
    return store.retention_policy()


def disable_stale_fixture_schedules() -> list[str]:
    """When VCF_DOCTOR_TEST_FIXTURES is off, a fixture-kind connection can only
    be a leftover from a removed test/demo hook: it has no live vCenter behind
    it, so its scheduled scans just error every interval until the operator
    deletes it (#33). Pause its schedule so the log stays quiet; the operator
    still sees and removes the connection itself on the Connections page.
    Returns the ids paused."""
    if settings.test_fixtures:
        return []
    paused = []
    for conn in store.list_connections():
        if conn.kind != "fixture":
            continue
        sched = store.get_schedule(conn.id)
        if sched is not None and sched.enabled:
            store.update_schedule(conn.id, enabled=False, clear_next_run=True)
            remove_job(conn.id)
            paused.append(conn.id)
    if paused:
        log.warning(
            "startup: paused schedule for %d leftover fixture connection(s); remove them",
            len(paused),
        )
    return paused


def _startup_vault_rekey() -> _StartupResult:
    from app import vault

    vault.rekey_at_startup()
    return _StartupResult()


def _startup_vault_plaintext() -> _StartupResult:
    from app import vault

    vault.migrate_plaintext()
    return _StartupResult()


def _startup_event_defaults() -> _StartupResult:
    from app.events import store as events_store

    events_store.seed_defaults()
    return _StartupResult()


def _startup_change_log_backfill() -> _StartupResult:
    for cid, start in store.backfill_log_since().items():
        log.info("change log coverage for %s starts %s", cid, start.isoformat())
    return _StartupResult()


def _startup_scan_reconciliation() -> _StartupResult:
    interrupted = store.reconcile_interrupted_runs()
    if interrupted:
        log.warning("marked %d interrupted scan run(s) as error", interrupted)
    return _StartupResult()


def _startup_auth_bootstrap() -> _StartupResult:
    from app import auth

    auth.bootstrap_from_env()
    return _StartupResult()


def _startup_fixture_schedules() -> _StartupResult:
    disable_stale_fixture_schedules()
    return _StartupResult()


def _startup_retention() -> _StartupResult:
    connections = store.list_connections()
    policy = retention_policy()
    blocked = False
    failed = False
    for conn in connections:
        if conn.id in _retention_completed:
            continue
        try:
            store.apply_retention(conn.id, policy)
        except Exception as exc:
            if db.is_connection_unavailable(exc):
                blocked = True
            else:
                failed = True
                log.exception(
                    "deferred startup step retention failed for connection %s; "
                    "retrying next interval",
                    conn.id,
                )
        else:
            _retention_completed.add(conn.id)
    return _StartupResult(failed=failed, blocked=blocked)


_STARTUP_STEPS = (
    ("vault_rekey", _startup_vault_rekey),
    ("vault_plaintext", _startup_vault_plaintext),
    ("event_defaults", _startup_event_defaults),
    ("change_log_backfill", _startup_change_log_backfill),
    ("scan_reconciliation", _startup_scan_reconciliation),
    ("auth_bootstrap", _startup_auth_bootstrap),
    ("fixture_schedules", _startup_fixture_schedules),
    ("retention", _startup_retention),
)


def startup_maintenance() -> bool:
    """Catch up persisted state after downtime before this worker becomes ready.

    Runs on every worker. Everything it does is idempotent, so N workers
    starting together repeat work rather than corrupt any.
    """
    global _startup_pending, _startup_failures

    pending = set(_startup_pending)
    failures: set[str] = set()
    blocked = False
    for identifier, action in _STARTUP_STEPS:
        if identifier not in pending:
            continue
        try:
            result = action()
        except Exception as exc:
            if db.is_connection_unavailable(exc):
                blocked = True
            else:
                failures.add(identifier)
                log.exception("deferred startup step %s failed; retrying next interval", identifier)
        else:
            if result.failed:
                failures.add(identifier)
            if result.blocked:
                blocked = True
            if not result.failed and not result.blocked:
                pending.remove(identifier)

    _startup_pending = frozenset(pending)
    _startup_failures = tuple(sorted(failures))
    if blocked:
        log.info("deferred startup work is waiting for the database")
    return blocked


def _begin_startup() -> None:
    global _startup_pending, _startup_failures
    _startup_pending = frozenset(identifier for identifier, _ in _STARTUP_STEPS)
    _startup_failures = ()
    _retention_completed.clear()


def compute_findings(resources: list[Resource], previous: list[Resource] | None) -> list[Finding]:
    """Call Agent C's registry when present; never let a check failure kill a scan."""
    try:
        from app.diagnostics.registry import run_all
    except ImportError:
        return []
    try:
        try:
            result = run_all(resources, previous)
        except TypeError:
            result = run_all(resources)
        return [f if isinstance(f, Finding) else Finding.model_validate(f) for f in result]
    except Exception:
        log.exception("diagnostics failed")
        return []


class ChangeDiffError(RuntimeError):
    pass


def compute_changes(old: list[Resource], new: list[Resource]) -> list:
    try:
        from app.diff.engine import diff
    except ImportError as exc:
        raise ChangeDiffError("change diff engine unavailable") from exc
    try:
        return list(diff(old, new))
    except Exception as exc:
        log.exception("diff failed")
        raise ChangeDiffError("change diff failed") from exc


def _label(trigger: str, label: str | None) -> str:
    if label:
        return label
    stamp = store.now().strftime("%Y-%m-%d %H:%M")
    return f"{'Scheduled' if trigger == 'scheduled' else 'Manual'} {stamp}"


NEEDS_PASSWORD = (
    "stored password cannot be decrypted with the current encryption key; "
    "re-enter the password for this connection"
)


def run_scan(connection_id: str, trigger: str = "manual", label: str | None = None) -> ScanRun:
    """Collect, persist a snapshot, cache findings, log changes, prune, record the run."""
    conn = store.get_connection(connection_id)
    if conn is None:
        raise KeyError(connection_id)
    if conn.credentials_unreadable:
        # Key lost or rotated: no traceback per interval, one skipped run that
        # says what to do. Cleared as soon as the password is re-entered.
        log.warning("scan skipped for %s: stored password needs re-entering", connection_id)
        run = store.create_run(connection_id, trigger, status="skipped")
        run = store.finish_run(run.id, "skipped", error=NEEDS_PASSWORD)
        store.update_schedule(connection_id, last_run=run.finished, last_status="skipped")
        _refresh_next_run(connection_id)
        return run
    with db.try_advisory_lock(db.SCAN_LOCK, connection_id) as acquired:
        if not acquired:
            run = store.create_run(connection_id, trigger, status="skipped")
            run = store.finish_run(run.id, "skipped", error="previous run still active")
            store.update_schedule(connection_id, last_status="skipped")
            return run
        return _run_scan_locked(conn, trigger, label)


def _run_scan_locked(conn, trigger: str, label: str | None) -> ScanRun:
    """The scan itself, with this connection's scan lock already held."""
    connection_id = conn.id
    run = store.create_run(connection_id, trigger)
    try:
        collector = get_collector(conn)
        previous = store.latest_snapshot(connection_id)
        resources = collector.collect()
        changes = compute_changes(previous.resources, resources) if previous is not None else []
        snapshot: Snapshot = store.save_snapshot(
            connection_id, resources, _label(trigger, label), scheduled=trigger == "scheduled"
        )
        findings = compute_findings(resources, previous.resources if previous else None)
        store.save_findings(snapshot.id, findings)
        # --- events capture (app/events); never fails the scan ---
        try:
            from app.events.service import capture_events

            capture_events(conn, collector, snapshot)
        except Exception:  # noqa: BLE001
            log.exception("event capture failed for %s", connection_id)
        # --- end events capture ---
        if previous is not None:
            store.save_changes(
                connection_id,
                previous.id,
                snapshot.id,
                snapshot.created_at,
                changes,
            )
        store.apply_retention(connection_id)
        run = store.finish_run(run.id, "ok", snapshot_id=snapshot.id)
        status = "ok"
    except Exception as exc:
        log.exception("scan failed for %s", connection_id)
        run = store.finish_run(run.id, "error", error=str(exc)[:500])
        status = "error"
    store.update_schedule(connection_id, last_run=run.finished, last_status=status)
    _refresh_next_run(connection_id)
    return run


def run_all_scans(trigger: str = "manual") -> list[ScanRun]:
    return [run_scan(c.id, trigger) for c in store.list_connections()]


# --- APScheduler ----------------------------------------------------------


def scheduler_enabled() -> bool:
    return os.environ.get("VCF_DOCTOR_SCHEDULER", "on").lower() not in ("0", "off", "false")


def _background_jobs_enabled() -> bool:
    return "pytest" not in sys.modules


def _job_id(connection_id: str) -> str:
    return f"scan:{connection_id}"


def _scheduled_job(connection_id: str) -> None:
    try:
        run_scan(connection_id, "scheduled")
    except KeyError:
        remove_job(connection_id)


def _refresh_next_run(connection_id: str) -> None:
    if not _leader or _scheduler is None:
        return
    job = _scheduler.get_job(_job_id(connection_id))
    if job is not None and job.next_run_time:
        store.update_schedule(connection_id, next_run=job.next_run_time)


def reschedule(connection_id: str) -> None:
    """(Re)create the interval job for a connection from its stored schedule.

    A no-op on a worker that does not hold the scheduler lock. That worker's
    saved change still reaches the leader, which re-reads the schedules in
    reconcile_jobs().
    """
    if not _leader or _scheduler is None:
        return
    from apscheduler.triggers.interval import IntervalTrigger

    sched = store.get_schedule(connection_id)
    remove_job(connection_id)
    if sched is None or not sched.enabled:
        _scheduled_state[connection_id] = (0, False) if sched is None else (
            sched.interval_minutes,
            False,
        )
        store.update_schedule(connection_id, clear_next_run=True)
        return
    _scheduled_state[connection_id] = (sched.interval_minutes, sched.enabled)
    minutes = max(sched.interval_minutes, settings.min_interval_minutes)
    now = store.now()
    start = now + timedelta(minutes=minutes)
    if sched.last_run is not None:
        resume = sched.last_run + timedelta(minutes=minutes)
        start = max(resume, now + timedelta(seconds=5))
    job = _scheduler.add_job(
        _scheduled_job,
        IntervalTrigger(minutes=minutes, start_date=start),
        id=_job_id(connection_id),
        args=[connection_id],
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    store.update_schedule(connection_id, next_run=job.next_run_time)


def remove_job(connection_id: str) -> None:
    if _scheduler is None:
        return
    if _scheduler.get_job(_job_id(connection_id)) is not None:
        _scheduler.remove_job(_job_id(connection_id))


def reconcile_jobs() -> None:
    """Bring the leader's jobs back in line with the stored schedules.

    Connections are added and schedules are edited through whichever worker
    served the request; only this one holds jobs, so it re-reads rather than
    being told.
    """
    if not _leader or _scheduler is None:
        return
    wanted: dict[str, tuple[int, bool]] = {}
    for conn in store.list_connections():
        sched = store.get_schedule(conn.id)
        wanted[conn.id] = (0, False) if sched is None else (sched.interval_minutes, sched.enabled)
    for connection_id in list(_scheduled_state):
        if connection_id not in wanted:
            remove_job(connection_id)
            _scheduled_state.pop(connection_id, None)
    for connection_id, state in wanted.items():
        if _scheduled_state.get(connection_id) != state:
            log.info("picking up schedule change for %s", connection_id)
            reschedule(connection_id)


def _drop_scan_jobs() -> None:
    for connection_id in list(_scheduled_state):
        remove_job(connection_id)
    _scheduled_state.clear()


def take_leadership() -> None:
    """Hold the scheduler lock if it is free, and keep the jobs in step with it.

    Runs every five seconds while startup work is blocked by the database, and
    once a minute otherwise. A leader whose lock session ended (a restarted or
    failed-over database) drops its jobs and competes for the lock again, so
    scheduled scans resume instead of stopping silently.
    """
    global _leader
    if _leader and not db.scheduler_lock_alive():
        log.warning("lost the scheduler lock; the database session ended. Dropping scan jobs")
        _leader = False
        _drop_scan_jobs()
    if not _leader:
        if not db.acquire_scheduler_lock():
            return  # another worker or pod has it
        _leader = True
        _scheduled_state.clear()
        log.info("holding the scheduler lock; scheduled scans run in this worker")
    try:
        interrupted = store.reconcile_interrupted_runs()
    except Exception as exc:
        if db.is_connection_unavailable(exc):
            log.info("scan run reconciliation is waiting for the database")
        else:
            log.exception("scan run reconciliation failed; retrying next interval")
    else:
        if interrupted:
            log.warning("marked %d interrupted scan run(s) as error", interrupted)
    reconcile_jobs()


def _leadership_job() -> None:
    try:
        take_leadership()
    except Exception as exc:
        if db.is_connection_unavailable(exc):
            log.info("scheduler leadership is waiting for the database")
        else:
            log.exception("scheduler leadership check failed; retrying next interval")


def _maintenance_job() -> None:
    global _startup_failures
    if _startup_pending:
        blocked = False
        try:
            blocked = startup_maintenance()
        except Exception as exc:
            # Last resort for a pass-level failure, which is logged rather than published.
            _startup_failures = ()
            if db.is_connection_unavailable(exc):
                blocked = True
                log.info("deferred startup work is waiting for the database")
            else:
                log.exception("deferred startup maintenance pass failed; retrying next interval")
        if not _startup_pending:
            log.info("deferred startup work completed")
        if _scheduler is not None:
            from apscheduler.triggers.interval import IntervalTrigger

            interval = STARTUP_RETRY_SECONDS if blocked else RECONCILE_SECONDS
            _scheduler.reschedule_job(
                "scheduler-maintenance",
                trigger=IntervalTrigger(seconds=interval),
            )
    if scheduler_enabled():
        _leadership_job()


def start() -> None:
    """Start the background scheduler. Every worker runs one; only the worker
    holding the advisory lock owns scan jobs."""
    global _scheduler
    if _scheduler is not None or not _background_jobs_enabled():
        return
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    _begin_startup()
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _maintenance_job,
        IntervalTrigger(seconds=STARTUP_RETRY_SECONDS),
        id="scheduler-maintenance",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC),
    )
    _scheduler.start()
    if scheduler_enabled():
        log.info("background startup and scheduler reconciliation started")
    else:
        log.info("background startup started; scheduled scans are disabled")


def shutdown() -> None:
    global _scheduler, _leader, _startup_pending, _startup_failures
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    _scheduled_state.clear()
    _leader = False
    _startup_pending = frozenset()
    _startup_failures = ()
    _retention_completed.clear()
    db.release_scheduler_lock()


def startup_status() -> tuple[bool, tuple[str, ...]]:
    return not _startup_pending, _startup_failures


def running() -> bool:
    """Whether scheduled scans are running anywhere in this deployment.

    Read from the lock rather than from this process, for two reasons. A worker
    that is not the leader still has to answer yes. And a leader whose database
    is unreachable is not scheduling anything, so it must not claim to be
    while the readiness answer beside it says the database is down.
    """
    return db.scheduler_lock_held()
