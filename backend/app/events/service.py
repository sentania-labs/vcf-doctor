"""Capture vCenter events and tasks once per scan.

capture_events() is called by the scan pipeline right after the snapshot is
saved. It queries from the last complete checkpoint with a small overlap,
resolves resource names against the snapshot, and stores events with id-based
deduplication. A per-connection checkpoint advances only after the full window
is fetched. Capped windows are bisected so the newest rows cannot be silently
discarded. Pruning and bounded reclamation run in the retention pass after
capture.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from app.collectors.vsphere.events import MAX_ITEMS
from app.events import store as events_store
from app.models.event import Event
from app.models.snapshot import Snapshot
from app.snapshots import store

log = logging.getLogger("vcf_doctor.events")

OVERLAP = timedelta(seconds=60)
MIN_WINDOW = timedelta(seconds=1)


def retention_hours() -> int:
    return events_store.event_policy().retention_hours


def capture_window(connection_id: str, snapshot: Snapshot) -> tuple[datetime, datetime]:
    """Return the checkpoint-based window, bounded by event retention."""
    until = snapshot.created_at or store.now()
    cutoff = until - timedelta(hours=retention_hours())
    checkpoint = events_store.capture_checkpoint(connection_id)
    since = checkpoint - OVERLAP if checkpoint else cutoff
    return max(min(since, until), cutoff), until


def _result(raw: Any) -> tuple[list[Event], bool]:
    """Accept the vSphere result and legacy list-returning collectors."""
    if hasattr(raw, "events") and hasattr(raw, "complete"):
        return list(raw.events), bool(raw.complete)
    rows = list(raw or [])
    return rows, len(rows) < MAX_ITEMS


def _fetch_window(
    connection_id: str,
    collect: Any,
    since: datetime,
    until: datetime,
) -> tuple[list[Event], bool]:
    raw = collect(since, until)
    rows, complete = _result(raw)
    unavailable = getattr(raw, "task_history_unavailable", None)
    if unavailable is not None:
        events_store.set_task_history_unavailable(connection_id, unavailable)
    error = getattr(raw, "error", None)
    if error:
        events_store.record_incomplete_interval(connection_id, since, until, error)
        return rows, False
    if complete:
        events_store.resolve_incomplete_range(connection_id, since, until)
        return rows, True
    if until - since <= MIN_WINDOW:
        events_store.record_incomplete_interval(
            connection_id,
            since,
            until,
            f"capture limit of {MAX_ITEMS} reached in minimum window",
        )
        return rows, False
    midpoint = since + (until - since) / 2
    left, left_complete = _fetch_window(connection_id, collect, since, midpoint)
    right, right_complete = _fetch_window(connection_id, collect, midpoint, until)
    return left + right, left_complete and right_complete


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
    """Fetch and store. Returns the number of new events stored.
    Collectors without collect_events() (older fixtures, other kinds) are skipped."""
    collect = getattr(collector, "collect_events", None)
    if collect is None:
        return 0
    connection_id = getattr(connection, "id", None) or snapshot.connection_id
    since, until = capture_window(connection_id, snapshot)
    cutoff = until - timedelta(hours=retention_hours())
    events_store.prune_incomplete_intervals(connection_id, cutoff)
    retry_rows: list[Event] = []
    for interval in events_store.capture_status(connection_id).incomplete_intervals:
        if since <= interval.since and interval.until <= until:
            continue
        try:
            retried, retry_complete = _fetch_window(
                connection_id, collect, interval.since, interval.until
            )
            retry_rows.extend(retried)
            if retry_complete:
                events_store.resolve_incomplete_range(
                    connection_id, interval.since, interval.until
                )
        except Exception as exc:  # noqa: BLE001
            events_store.record_incomplete_interval(
                connection_id, interval.since, interval.until, str(exc)[:500]
            )
    try:
        raw, complete = _fetch_window(connection_id, collect, since, until)
    except Exception as exc:  # noqa: BLE001  (never fail the scan)
        log.warning("event capture for %s failed: %s", connection_id, exc)
        retried_events = [
            e for e in enrich(retry_rows, connection_id, snapshot) if e.time >= cutoff
        ]
        return events_store.upsert_events(retried_events)
    events = [
        e for e in enrich(retry_rows + list(raw), connection_id, snapshot) if e.time >= cutoff
    ]
    inserted = events_store.upsert_events(events)
    if complete:
        events_store.resolve_incomplete_range(connection_id, since, until)
        events_store.set_capture_checkpoint(connection_id, until)
    log.info(
        "events %s: window %s..%s, %d fetched, %d new, complete=%s",
        connection_id,
        since.isoformat(timespec="seconds"),
        until.isoformat(timespec="seconds"),
        len(events),
        inserted,
        complete,
    )
    return inserted
