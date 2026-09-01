"""SQLite access shared by every backend module.

Agent A owns schema for snapshots, connections, schedules, scan runs.
The settings table below is a generic key/value store used by retention
and the assistant so no agent blocks on another for GUI-editable config.
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.config import settings as cfg

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(cfg.db_path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            _conn.commit()
        return _conn


def reset_for_tests(path: str) -> None:
    """Point the module at a fresh database. Tests only."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
    cfg.db_path = path


def get_setting(key: str, default: Any = None) -> Any:
    row = connect().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value: Any) -> None:
    c = connect()
    with _lock:
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        c.commit()
