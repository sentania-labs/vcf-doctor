"""SQLite access shared by every backend module.

Agent A owns schema for snapshots, connections, schedules, scan runs.
The settings table below is a generic key/value store used by retention
and the assistant so no agent blocks on another for GUI-editable config.

Concurrency model: one shared sqlite3 connection, one process-wide RLock
around every write transaction (see `transaction()`), WAL journal so reads
from the API threads do not block the scheduler thread.
"""

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.config import settings as cfg

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    username TEXT NOT NULL,
    password TEXT NOT NULL,
    verify_tls INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'vcenter',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedules (
    connection_id TEXT PRIMARY KEY REFERENCES connections(id) ON DELETE CASCADE,
    interval_minutes INTEGER NOT NULL DEFAULT 15,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run TEXT,
    next_run TEXT,
    last_status TEXT
);
CREATE TABLE IF NOT EXISTS scan_runs (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    started TEXT NOT NULL,
    finished TEXT,
    status TEXT NOT NULL,
    error TEXT,
    snapshot_id TEXT,
    trigger TEXT NOT NULL DEFAULT 'manual'
);
CREATE INDEX IF NOT EXISTS ix_scan_runs_conn ON scan_runs(connection_id, started);
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    label TEXT NOT NULL,
    scheduled INTEGER NOT NULL DEFAULT 0,
    resource_count INTEGER NOT NULL DEFAULT 0,
    resources TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snapshots_conn ON snapshots(connection_id, created_at);
CREATE TABLE IF NOT EXISTS findings (
    snapshot_id TEXT PRIMARY KEY REFERENCES snapshots(id) ON DELETE CASCADE,
    findings TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(cfg.db_path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Serialize a write against every other writer in the process."""
    c = connect()
    with _lock:
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def fetchone(sql: str, args: tuple = ()) -> sqlite3.Row | None:
    """Locked read. Cursors on the shared connection are not thread safe."""
    with _lock:
        return connect().execute(sql, args).fetchone()


def fetchall(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return connect().execute(sql, args).fetchall()


def reset_for_tests(path: str) -> None:
    """Point the module at a fresh database. Tests only."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
    cfg.db_path = path


def get_setting(key: str, default: Any = None) -> Any:
    row = fetchone("SELECT value FROM settings WHERE key = ?", (key,))
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value: Any) -> None:
    with transaction() as c:
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
