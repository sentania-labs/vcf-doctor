"""Persistence for vCenter events and tasks.

The `events` table is created lazily on first use (CREATE TABLE IF NOT
EXISTS through the shared db connection) so this package owns its own
schema and nothing else has to know about it. Times are stored as ISO 8601
UTC strings so lexical ORDER BY and range compares are correct.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta

from app import db
from app.config import settings as cfg
from app.models.event import (
    Event,
    EventCaptureStatus,
    EventMaintenanceStatus,
    EventPolicy,
    IncompleteInterval,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    time TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'event',
    type TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL DEFAULT '',
    user TEXT,
    resource_id TEXT,
    resource_name TEXT,
    resource_type TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_conn_time ON events(connection_id, time);
CREATE INDEX IF NOT EXISTS ix_events_resource ON events(resource_id, time);
CREATE TABLE IF NOT EXISTS event_capture_state (
    connection_id TEXT PRIMARY KEY,
    last_complete_end TEXT
);
CREATE TABLE IF NOT EXISTS event_incomplete_intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id TEXT NOT NULL,
    since TEXT NOT NULL,
    until TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(connection_id, since, until)
);
CREATE INDEX IF NOT EXISTS ix_event_incomplete_conn
    ON event_incomplete_intervals(connection_id, since);
CREATE TABLE IF NOT EXISTS event_maintenance (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_run TEXT,
    last_error TEXT,
    pages_reclaimed INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO event_maintenance(id, pages_reclaimed) VALUES(1, 0);
"""

DEFAULT_LIMIT = 500
MAX_LIMIT = 5000
CATEGORIES = ("info", "warning", "error", "user")
EVENT_POLICY_KEY = "event_policy"

_schema_lock = threading.Lock()
_schema_conn: sqlite3.Connection | None = None  # the connection the schema was applied to


def ensure_schema() -> sqlite3.Connection:
    """Apply the schema once per db connection (tests swap the connection)."""
    global _schema_conn
    conn = db.connect()
    with _schema_lock:
        if _schema_conn is not conn:
            with db.transaction() as c:
                c.executescript(SCHEMA)
            _schema_conn = conn
    if db.get_setting(EVENT_POLICY_KEY) is None:
        set_event_policy(default_event_policy())
    return conn


def default_event_policy() -> EventPolicy:
    return EventPolicy(retention_hours=cfg.event_retention_hours, row_cap=cfg.event_row_cap)


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


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _row_to_event(row: sqlite3.Row) -> Event:
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
    ensure_schema()
    inserted = 0
    with db.transaction() as c:
        for e in events:
            cur = c.execute(
                "INSERT OR IGNORE INTO events(id, connection_id, time, source, type, category, "
                "message, user, resource_id, resource_name, resource_type) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
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
                ),
            )
            inserted += cur.rowcount
    return inserted


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
    ensure_schema()
    where: list[str] = []
    args: list[object] = []
    if connection_id:
        where.append("connection_id = ?")
        args.append(connection_id)
    if since is not None:
        where.append("time >= ?")
        args.append(_iso(since))
    if until is not None:
        where.append("time <= ?")
        args.append(_iso(until))
    if resource_id:
        where.append("resource_id = ?")
        args.append(resource_id)
    if category:
        where.append("category = ?")
        args.append(category)
    if q:
        needle = f"%{q.lower()}%"
        where.append(
            "(LOWER(message) LIKE ? OR LOWER(COALESCE(user, '')) LIKE ? "
            "OR LOWER(COALESCE(resource_name, '')) LIKE ?)"
        )
        args += [needle, needle, needle]
    sql = "SELECT * FROM events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY time DESC, id DESC LIMIT ?"
    args.append(max(1, min(int(limit), MAX_LIMIT)))
    return [_row_to_event(r) for r in db.fetchall(sql, tuple(args))]


def count_events(connection_id: str | None = None) -> int:
    ensure_schema()
    if connection_id:
        row = db.fetchone(
            "SELECT COUNT(*) AS n FROM events WHERE connection_id = ?", (connection_id,)
        )
    else:
        row = db.fetchone("SELECT COUNT(*) AS n FROM events")
    return int(row["n"])


def latest_event_time(connection_id: str) -> datetime | None:
    ensure_schema()
    row = db.fetchone("SELECT MAX(time) AS t FROM events WHERE connection_id = ?", (connection_id,))
    return _dt(row["t"]) if row and row["t"] else None


def prune_events(connection_id: str, hours: int, now: datetime | None = None) -> int:
    """Delete a connection's events older than the independent event window."""
    ensure_schema()
    cutoff = (now or datetime.now(UTC)) - timedelta(hours=max(int(hours), 0))
    with db.transaction() as c:
        cur = c.execute(
            "DELETE FROM events WHERE connection_id = ? AND time < ?",
            (connection_id, _iso(cutoff)),
        )
        return cur.rowcount


def enforce_row_cap(connection_id: str, row_cap: int) -> int:
    """Keep the newest `row_cap` events for one connection."""
    ensure_schema()
    with db.transaction() as c:
        cur = c.execute(
            "DELETE FROM events WHERE id IN ("
            "SELECT id FROM events WHERE connection_id = ? "
            "ORDER BY time DESC, id DESC LIMIT -1 OFFSET ?)",
            (connection_id, max(1, int(row_cap))),
        )
        return cur.rowcount


def capture_checkpoint(connection_id: str) -> datetime | None:
    ensure_schema()
    row = db.fetchone(
        "SELECT last_complete_end FROM event_capture_state WHERE connection_id = ?",
        (connection_id,),
    )
    return _dt(row["last_complete_end"]) if row and row["last_complete_end"] else None


def set_capture_checkpoint(connection_id: str, end: datetime) -> None:
    ensure_schema()
    with db.transaction() as c:
        c.execute(
            "INSERT INTO event_capture_state(connection_id, last_complete_end) VALUES(?, ?) "
            "ON CONFLICT(connection_id) DO UPDATE SET "
            "last_complete_end = excluded.last_complete_end",
            (connection_id, _iso(end)),
        )


def record_incomplete_interval(
    connection_id: str,
    since: datetime,
    until: datetime,
    error: str | None = None,
    *,
    at: datetime | None = None,
) -> None:
    ensure_schema()
    stamp = _iso(at or datetime.now(UTC))
    with db.transaction() as c:
        start, end = _iso(since), _iso(until)
        attempts = 0
        while True:
            overlaps = c.execute(
                "SELECT * FROM event_incomplete_intervals "
                "WHERE connection_id = ? AND since <= ? AND until >= ?",
                (connection_id, end, start),
            ).fetchall()
            if not overlaps:
                break
            start = min(start, *(row["since"] for row in overlaps))
            end = max(end, *(row["until"] for row in overlaps))
            attempts = max(attempts, *(row["attempts"] for row in overlaps))
            c.executemany(
                "DELETE FROM event_incomplete_intervals WHERE id = ?",
                [(row["id"],) for row in overlaps],
            )
        c.execute(
            "INSERT INTO event_incomplete_intervals"
            "(connection_id, since, until, attempts, last_error, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (connection_id, start, end, attempts + 1, error, stamp),
        )


def resolve_incomplete_range(connection_id: str, since: datetime, until: datetime) -> int:
    """Clear recorded gaps proven covered by one complete query."""
    ensure_schema()
    with db.transaction() as c:
        return c.execute(
            "DELETE FROM event_incomplete_intervals "
            "WHERE connection_id = ? AND since >= ? AND until <= ?",
            (connection_id, _iso(since), _iso(until)),
        ).rowcount


def prune_incomplete_intervals(connection_id: str, before: datetime) -> int:
    ensure_schema()
    with db.transaction() as c:
        return c.execute(
            "DELETE FROM event_incomplete_intervals WHERE connection_id = ? AND until < ?",
            (connection_id, _iso(before)),
        ).rowcount


def capture_status(connection_id: str) -> EventCaptureStatus:
    ensure_schema()
    rows = db.fetchall(
        "SELECT * FROM event_incomplete_intervals WHERE connection_id = ? ORDER BY since",
        (connection_id,),
    )
    return EventCaptureStatus(
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


def bounded_maintenance(*, at: datetime | None = None, pages: int = 1000) -> EventMaintenanceStatus:
    """Reclaim at most `pages` free pages. Never performs a full VACUUM."""
    ensure_schema()
    stamp = at or datetime.now(UTC)
    before = int(db.fetchone("PRAGMA freelist_count")[0])
    try:
        with db.transaction() as c:
            if c.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
                migration = db.get_setting(db.COMPACTION_MIGRATION_KEY, {})
                raise RuntimeError(
                    migration.get("last_error") or "compaction unavailable: migration required"
                )
            c.execute(f"PRAGMA incremental_vacuum({max(1, int(pages))})")
        after = int(db.fetchone("PRAGMA freelist_count")[0])
        reclaimed = max(0, before - after)
        with db.transaction() as c:
            c.execute(
                "UPDATE event_maintenance SET last_run = ?, last_error = NULL, "
                "pages_reclaimed = ? WHERE id = 1",
                (_iso(stamp), reclaimed),
            )
    except Exception as exc:
        with db.transaction() as c:
            c.execute(
                "UPDATE event_maintenance SET last_run = ?, last_error = ? WHERE id = 1",
                (_iso(stamp), str(exc)[:500]),
            )
    return maintenance_status()


def maintenance_status() -> EventMaintenanceStatus:
    ensure_schema()
    row = db.fetchone("SELECT * FROM event_maintenance WHERE id = 1")
    migration = db.get_setting(db.COMPACTION_MIGRATION_KEY, {})
    return EventMaintenanceStatus(
        last_run=_dt(row["last_run"]) if row and row["last_run"] else None,
        last_error=migration.get("last_error") or (row["last_error"] if row else None),
        migration_required=int(db.fetchone("PRAGMA auto_vacuum")[0]) == 0,
        pages_reclaimed=int(row["pages_reclaimed"]) if row else 0,
    )


def delete_events(connection_id: str) -> int:
    """Remove every event for a connection (connection deleted)."""
    ensure_schema()
    with db.transaction() as c:
        cur = c.execute("DELETE FROM events WHERE connection_id = ?", (connection_id,))
        c.execute("DELETE FROM event_capture_state WHERE connection_id = ?", (connection_id,))
        c.execute(
            "DELETE FROM event_incomplete_intervals WHERE connection_id = ?", (connection_id,)
        )
        return cur.rowcount
