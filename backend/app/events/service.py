"""Capture vCenter events and tasks once per scan.

capture_events() is called by the scan pipeline right after the snapshot is
saved. It asks the collector for the window (previous snapshot time - 60 s,
now], or the last 24 h on a connection's first scan, resolves resource names
against the snapshot, stores the events (dedup on id) and prunes anything
older than the retention policy's daily_days. It never raises: a failure to
read events must not fail a scan. Pruning also runs from
store.apply_retention (startup and after every scan), so a connection whose
event fetch fails is still kept within the policy.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.events import store as events_store
from app.models.event import Event
from app.models.snapshot import Snapshot
from app.snapshots import store

log = logging.getLogger("vcf_doctor.events")

FIRST_SCAN_WINDOW = timedelta(hours=24)
OVERLAP = timedelta(seconds=60)


def retention_days() -> int:
    """The effective retention policy's daily_days: the stored policy, or the
    deployment defaults (VCF_DOCTOR_RETENTION_DAILY_DAYS) when none is stored
    or the stored one is invalid. Same source of truth as snapshot pruning."""
    return store.retention_policy().daily_days


def capture_window(connection_id: str, snapshot: Snapshot) -> tuple[datetime, datetime]:
    """(since, until) for this scan. `snapshot` is the one just saved, so the
    previous one is the second newest summary."""
    until = snapshot.created_at or store.now()
    previous = [s for s in store.list_snapshots(connection_id) if s.id != snapshot.id]
    if not previous:
        return until - FIRST_SCAN_WINDOW, until
    last = max(s.created_at for s in previous)
    return last - OVERLAP, until


def enrich(events: list[Event], connection_id: str, snapshot: Snapshot) -> list[Event]:
    """Stamp the connection id and fill resource_name / resource_type from the
    snapshot when the collector only knew the id."""
    by_id = {r.id: r for r in snapshot.resources}
    out: list[Event] = []
    for e in events:
        update: dict[str, Any] = {}
        if e.connection_id != connection_id:
            update["connection_id"] = connection_id
        res = by_id.get(e.resource_id) if e.resource_id else None
        if res is not None:
            if not e.resource_name:
                update["resource_name"] = res.name
            if not e.resource_type:
                update["resource_type"] = res.type
        out.append(e.model_copy(update=update) if update else e)
    return out


def capture_events(connection: Any, collector: Any, snapshot: Snapshot) -> int:
    """Fetch, store and prune. Returns the number of new events stored.
    Collectors without collect_events() (older fixtures, other kinds) are skipped."""
    collect = getattr(collector, "collect_events", None)
    if collect is None:
        return 0
    connection_id = getattr(connection, "id", None) or snapshot.connection_id
    since, until = capture_window(connection_id, snapshot)
    try:
        raw = collect(since, until) or []
    except Exception as exc:  # noqa: BLE001  (never fail the scan)
        log.warning("event capture for %s failed: %s", connection_id, exc)
        return 0
    days = retention_days()
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    events = [e for e in enrich(list(raw), connection_id, snapshot) if e.time >= cutoff]
    inserted = events_store.upsert_events(events)
    pruned = events_store.prune_events(connection_id, days, now=now)
    log.info(
        "events %s: window %s..%s, %d fetched, %d new, %d pruned",
        connection_id,
        since.isoformat(timespec="seconds"),
        until.isoformat(timespec="seconds"),
        len(events),
        inserted,
        pruned,
    )
    return inserted
