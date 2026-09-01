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
from app.models.event import Event

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
"""

DEFAULT_LIMIT = 500
MAX_LIMIT = 5000
CATEGORIES = ("info", "warning", "error", "user")

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
    return conn


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


def prune_events(connection_id: str, days: int, now: datetime | None = None) -> int:
    """Delete a connection's events older than `days`. Returns rows removed.
    The retention pass calls this with retention_policy.daily_days."""
    ensure_schema()
    cutoff = (now or datetime.now(UTC)) - timedelta(days=max(int(days), 0))
    with db.transaction() as c:
        cur = c.execute(
            "DELETE FROM events WHERE connection_id = ? AND time < ?",
            (connection_id, _iso(cutoff)),
        )
        return cur.rowcount


def delete_events(connection_id: str) -> int:
    """Remove every event for a connection (connection deleted)."""
    ensure_schema()
    with db.transaction() as c:
        cur = c.execute("DELETE FROM events WHERE connection_id = ?", (connection_id,))
        return cur.rowcount
