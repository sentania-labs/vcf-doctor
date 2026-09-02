"""Scan pipeline and APScheduler wiring.

run_scan() is the single code path for scheduled and manual captures.
The scheduler holds one interval job per enabled connection; its state is
reloaded from SQLite at startup so a container restart resumes unattended.
"""

import logging
import os
import sys
import threading
from datetime import timedelta

from app.collectors.registry import get_collector
from app.config import settings
from app.models import Finding, Resource, ScanRun, Snapshot
from app.models.snapshot import RetentionPolicy
from app.snapshots import store

log = logging.getLogger("vcf_doctor.scan")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_scheduler = None


def _lock_for(connection_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(connection_id, threading.Lock())


def retention_policy() -> RetentionPolicy:
    """Effective tier policy (settings KV `retention_policy`, else env defaults)."""
    return store.retention_policy()


def startup_maintenance() -> None:
    """Once per process start: compress legacy snapshot rows, then apply
    retention to every connection so a long-stopped instance catches up."""
    try:
        migrated = store.migrate_legacy_snapshots()
        if migrated:
            log.info("startup: compressed %d legacy snapshot(s)", migrated)
    except Exception:
        log.exception("startup: legacy snapshot migration failed")
    policy = retention_policy()
    for conn in store.list_connections():
        try:
            store.apply_retention(conn.id, policy)
        except Exception:
            log.exception("startup: retention failed for %s", conn.id)


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


def compute_changes(old: list[Resource], new: list[Resource]) -> list:
    try:
        from app.diff.engine import diff
    except ImportError:
        return []
    try:
        return list(diff(old, new))
    except Exception:
        log.exception("diff failed")
        return []


def _label(trigger: str, label: str | None) -> str:
    if label:
        return label
    stamp = store.now().strftime("%Y-%m-%d %H:%M")
    return f"{'Scheduled' if trigger == 'scheduled' else 'Manual'} {stamp}"


def run_scan(connection_id: str, trigger: str = "manual", label: str | None = None) -> ScanRun:
    """Collect, persist a snapshot, cache findings, log changes, prune, record the run."""
    conn = store.get_connection(connection_id)
    if conn is None:
        raise KeyError(connection_id)
    lock = _lock_for(connection_id)
    if not lock.acquire(blocking=False):
        run = store.create_run(connection_id, trigger, status="skipped")
        run = store.finish_run(run.id, "skipped", error="previous run still active")
        store.update_schedule(connection_id, last_status="skipped")
        return run
    try:
        run = store.create_run(connection_id, trigger)
        try:
            collector = get_collector(conn)
            previous = store.latest_snapshot(connection_id)
            resources = collector.collect()
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
                    compute_changes(previous.resources, resources),
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
    finally:
        lock.release()


def run_all_scans(trigger: str = "manual") -> list[ScanRun]:
    return [run_scan(c.id, trigger) for c in store.list_connections()]


# --- APScheduler ----------------------------------------------------------


def scheduler_enabled() -> bool:
    if os.environ.get("VCF_DOCTOR_SCHEDULER", "on").lower() in ("0", "off", "false"):
        return False
    return "pytest" not in sys.modules


def _job_id(connection_id: str) -> str:
    return f"scan:{connection_id}"


def _scheduled_job(connection_id: str) -> None:
    try:
        run_scan(connection_id, "scheduled")
    except KeyError:
        remove_job(connection_id)


def _refresh_next_run(connection_id: str) -> None:
    if _scheduler is None:
        return
    job = _scheduler.get_job(_job_id(connection_id))
    if job is not None and job.next_run_time:
        store.update_schedule(connection_id, next_run=job.next_run_time)


def reschedule(connection_id: str) -> None:
    """(Re)create the interval job for a connection from its stored schedule."""
    if _scheduler is None:
        return
    from apscheduler.triggers.interval import IntervalTrigger

    sched = store.get_schedule(connection_id)
    remove_job(connection_id)
    if sched is None or not sched.enabled:
        store.update_schedule(connection_id, clear_next_run=True)
        return
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


def start() -> None:
    global _scheduler
    if _scheduler is not None or not scheduler_enabled():
        return
    from apscheduler.schedulers.background import BackgroundScheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.start()
    for conn in store.list_connections():
        reschedule(conn.id)
    log.info("scheduler started with %d connections", len(store.list_connections()))


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def running() -> bool:
    return _scheduler is not None
