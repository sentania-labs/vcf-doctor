"""Persistence for vCenter events and tasks.

The tables live in the numbered migrations under `app/migrations`. Times are
stored as ISO 8601 UTC strings so lexical ORDER BY and range compares are
correct.

`time` and `user` are quoted in every statement below: both are keywords in
PostgreSQL and an unquoted `user` reads as the current_user function.

There is no bounded-vacuum bookkeeping here any more. Reclaiming space after a
delete is PostgreSQL's autovacuum, not the application's, so retention deletes
rows and stops there.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from functools import cache

from app import db
from app.config import settings as cfg
from app.models.event import (
    EVENT_RETENTION_HOURS_MAX,
    EVENT_RETENTION_HOURS_MIN,
    EVENT_ROW_CAP_MAX,
    EVENT_ROW_CAP_MIN,
    Event,
    EventCaptureStatus,
    EventPolicy,
    IncompleteInterval,
)

log = logging.getLogger("vcf_doctor.events")

DEFAULT_LIMIT = 500
MAX_LIMIT = 5000
CATEGORIES = ("info", "warning", "error", "user")
EVENT_POLICY_KEY = "event_policy"

_EVENT_COLUMNS = (
    'id, connection_id, "time", source, type, category, message, "user", '
    "resource_id, resource_name, resource_type"
)


@cache
def _bounded_default_values(retention_hours: int, row_cap: int) -> tuple[int, int]:
    bounded_hours = min(max(retention_hours, EVENT_RETENTION_HOURS_MIN), EVENT_RETENTION_HOURS_MAX)
    bounded_cap = min(max(row_cap, EVENT_ROW_CAP_MIN), EVENT_ROW_CAP_MAX)
    if (bounded_hours, bounded_cap) != (retention_hours, row_cap):
        log.warning(
            "event policy defaults clamped from retention_hours=%d row_cap=%d "
            "to retention_hours=%d row_cap=%d",
            retention_hours,
            row_cap,
            bounded_hours,
            bounded_cap,
        )
    return bounded_hours, bounded_cap


def default_event_policy() -> EventPolicy:
    retention_hours, row_cap = _bounded_default_values(
        cfg.event_retention_hours,
        cfg.event_row_cap,
    )
    return EventPolicy(retention_hours=retention_hours, row_cap=row_cap)


def event_policy() -> EventPolicy:
    raw = db.get_setting(EVENT_POLICY_KEY)
    if raw:
        try:
            return EventPolicy.model_validate(raw)
        except Exception:  # noqa: BLE001  invalid stored settings fall back safely
            pass
    return default_event_policy()


def set_event_policy(policy: EventPolicy) -> EventPolicy:
    db.set_setting(EVENT_POLICY_KEY, policy.model_dump())
    return policy


def seed_defaults() -> None:
    """Store the default event policy on a fresh database, so Settings shows a
    saved value rather than an implicit fallback on the first visit."""
    if db.get_setting(EVENT_POLICY_KEY) is None:
        set_event_policy(default_event_policy())


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _row_to_event(row: dict) -> Event:
    return Event(
        id=row["id"],
        connection_id=row["connection_id"],
        time=_dt(row["time"]),
        source=row["source"],
        type=row["type"],
        category=row["category"],
        message=row["message"],
        user=row["user"],
        resource_id=row["resource_id"],
        resource_name=row["resource_name"],
        resource_type=row["resource_type"],
    )


def upsert_events(events: list[Event]) -> int:
    """Insert events, ignoring ids already present. Returns how many were new."""
    if not events:
        return 0
    with db.transaction() as c:
        c.executemany(
            f"INSERT INTO events({_EVENT_COLUMNS}) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
            [
                (
                    e.id,
                    e.connection_id,
                    _iso(e.time),
                    e.source,
                    e.type,
                    e.category,
                    e.message,
                    e.user,
                    e.resource_id,
                    e.resource_name,
                    e.resource_type,
                )
                for e in events
            ],
        )
        return c.rowcount


def list_events(
    connection_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    resource_id: str | None = None,
    category: str | None = None,
    q: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[Event]:
    """Newest first. `q` matches message, user and resource_name, case-insensitive."""
    where: list[str] = []
    args: list[object] = []
    if connection_id:
        where.append("connection_id = %s")
        args.append(connection_id)
    if since is not None:
        where.append('"time" >= %s')
        args.append(_iso(since))
    if until is not None:
        where.append('"time" <= %s')
        args.append(_iso(until))
    if resource_id:
        where.append("resource_id = %s")
        args.append(resource_id)
    if category:
        where.append("category = %s")
        args.append(category)
    if q:
        needle = f"%{q.lower()}%"
        where.append(
            "(LOWER(message) LIKE %s OR LOWER(COALESCE(\"user\", '')) LIKE %s "
            "OR LOWER(COALESCE(resource_name, '')) LIKE %s)"
        )
        args += [needle, needle, needle]
    sql = "SELECT * FROM events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += ' ORDER BY "time" DESC, id DESC LIMIT %s'
    args.append(max(1, min(int(limit), MAX_LIMIT)))
    return [_row_to_event(r) for r in db.fetchall(sql, tuple(args))]


def count_events(connection_id: str | None = None) -> int:
    if connection_id:
        row = db.fetchone(
            "SELECT COUNT(*) AS n FROM events WHERE connection_id = %s", (connection_id,)
        )
    else:
        row = db.fetchone("SELECT COUNT(*) AS n FROM events")
    return int(row["n"])


def latest_event_time(connection_id: str) -> datetime | None:
    row = db.fetchone(
        'SELECT MAX("time") AS t FROM events WHERE connection_id = %s', (connection_id,)
    )
    return _dt(row["t"]) if row and row["t"] else None


def prune_events(connection_id: str, hours: int, now: datetime | None = None) -> int:
    """Delete a connection's events older than the independent event window."""
    cutoff = (now or datetime.now(UTC)) - timedelta(hours=max(int(hours), 0))
    with db.transaction() as c:
        c.execute(
            'DELETE FROM events WHERE connection_id = %s AND "time" < %s',
            (connection_id, _iso(cutoff)),
        )
        return c.rowcount


def enforce_row_cap(connection_id: str, row_cap: int) -> int:
    """Keep the newest `row_cap` events for one connection."""
    with db.transaction() as c:
        c.execute(
            "DELETE FROM events WHERE id IN ("
            "SELECT id FROM events WHERE connection_id = %s "
            'ORDER BY "time" DESC, id DESC OFFSET %s)',
            (connection_id, max(1, int(row_cap))),
        )
        return c.rowcount


def capture_checkpoint(connection_id: str) -> datetime | None:
    row = db.fetchone(
        "SELECT last_complete_end FROM event_capture_state WHERE connection_id = %s",
        (connection_id,),
    )
    return _dt(row["last_complete_end"]) if row and row["last_complete_end"] else None


def set_capture_checkpoint(connection_id: str, end: datetime) -> None:
    with db.transaction() as c:
        c.execute(
            "INSERT INTO event_capture_state(connection_id, last_complete_end) VALUES(%s, %s) "
            "ON CONFLICT(connection_id) DO UPDATE SET "
            "last_complete_end = excluded.last_complete_end",
            (connection_id, _iso(end)),
        )


def set_task_history_unavailable(connection_id: str, unavailable: bool) -> None:
    with db.transaction() as c:
        c.execute(
            "INSERT INTO event_capture_state(connection_id, task_history_unavailable) "
            "VALUES(%s, %s) ON CONFLICT(connection_id) DO UPDATE SET "
            "task_history_unavailable = excluded.task_history_unavailable",
            (connection_id, bool(unavailable)),
        )


def record_incomplete_interval(
    connection_id: str,
    since: datetime,
    until: datetime,
    error: str | None = None,
    *,
    at: datetime | None = None,
) -> None:
    stamp = _iso(at or datetime.now(UTC))
    with db.transaction() as c:
        # Read, merge, rewrite. Two workers recording overlapping gaps for the
        # same connection would otherwise interleave and leave both rows.
        db.lock_in_transaction(c, "event_gaps", connection_id)
        start, end = _iso(since), _iso(until)
        attempts = 0
        while True:
            overlaps = c.execute(
                "SELECT * FROM event_incomplete_intervals "
                "WHERE connection_id = %s AND since <= %s AND until >= %s",
                (connection_id, end, start),
            ).fetchall()
            if not overlaps:
                break
            start = min(start, *(row["since"] for row in overlaps))
            end = max(end, *(row["until"] for row in overlaps))
            attempts = max(attempts, *(row["attempts"] for row in overlaps))
            c.executemany(
                "DELETE FROM event_incomplete_intervals WHERE id = %s",
                [(row["id"],) for row in overlaps],
            )
        c.execute(
            "INSERT INTO event_incomplete_intervals"
            "(connection_id, since, until, attempts, last_error, updated_at) "
            "VALUES(%s, %s, %s, %s, %s, %s)",
            (connection_id, start, end, attempts + 1, error, stamp),
        )


def resolve_incomplete_range(connection_id: str, since: datetime, until: datetime) -> int:
    """Clear recorded gaps proven covered by one complete query."""
    with db.transaction() as c:
        c.execute(
            "DELETE FROM event_incomplete_intervals "
            "WHERE connection_id = %s AND since >= %s AND until <= %s",
            (connection_id, _iso(since), _iso(until)),
        )
        return c.rowcount


def prune_incomplete_intervals(connection_id: str, before: datetime) -> int:
    with db.transaction() as c:
        cutoff = _iso(before)
        c.execute(
            "DELETE FROM event_incomplete_intervals WHERE connection_id = %s AND until < %s",
            (connection_id, cutoff),
        )
        deleted = c.rowcount
        c.execute(
            "UPDATE event_incomplete_intervals SET since = %s "
            "WHERE connection_id = %s AND since < %s",
            (cutoff, connection_id, cutoff),
        )
        return deleted


def capture_status(connection_id: str) -> EventCaptureStatus:
    rows = db.fetchall(
        "SELECT * FROM event_incomplete_intervals WHERE connection_id = %s ORDER BY since",
        (connection_id,),
    )
    state = db.fetchone(
        "SELECT task_history_unavailable FROM event_capture_state WHERE connection_id = %s",
        (connection_id,),
    )
    return EventCaptureStatus(
        task_history_unavailable=bool(state and state["task_history_unavailable"]),
        connection_id=connection_id,
        last_complete_end=capture_checkpoint(connection_id),
        incomplete_intervals=[
            IncompleteInterval(
                id=r["id"],
                connection_id=r["connection_id"],
                since=_dt(r["since"]),
                until=_dt(r["until"]),
                attempts=r["attempts"],
                last_error=r["last_error"],
                updated_at=_dt(r["updated_at"]),
            )
            for r in rows
        ],
    )


def delete_events(connection_id: str) -> int:
    """Remove every event for a connection (connection deleted)."""
    with db.transaction() as c:
        c.execute("DELETE FROM events WHERE connection_id = %s", (connection_id,))
        deleted = c.rowcount
        c.execute("DELETE FROM event_capture_state WHERE connection_id = %s", (connection_id,))
        c.execute(
            "DELETE FROM event_incomplete_intervals WHERE connection_id = %s", (connection_id,)
        )
        return deleted
