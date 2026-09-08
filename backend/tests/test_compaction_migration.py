import math
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db
from app.events import store as events_store
from app.main import app


@pytest.fixture()
def legacy(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE retained_data (value TEXT)")
        c.execute("INSERT INTO retained_data VALUES (?)", ("existing installation",))
        assert c.execute("PRAGMA auto_vacuum").fetchone()[0] == 0
    db.reset_for_tests(str(path))
    return path


def test_startup_migrates_once_and_preserves_data(legacy, monkeypatch):
    space_checks = []

    def available(path):
        assert path == legacy.parent
        required = math.ceil(legacy.stat().st_size * 1.5)
        space_checks.append(required)
        return SimpleNamespace(free=required)

    monkeypatch.setattr(db.shutil, "disk_usage", available)
    db.connect()
    assert db.fetchone("PRAGMA auto_vacuum")[0] == 2
    assert db.fetchone("SELECT value FROM retained_data")[0] == "existing installation"
    marker = db.get_setting(db.COMPACTION_MIGRATION_KEY)
    assert marker["migrated_at"]
    assert marker["last_error"] is None
    db.reset_for_tests(str(legacy))
    db.connect()
    db.migrate_compaction()
    assert db.get_setting(db.COMPACTION_MIGRATION_KEY) == marker
    assert len(space_checks) == 1
    assert not events_store.maintenance_status().migration_required
    with db.transaction() as c:
        c.execute("CREATE TABLE disposable (payload BLOB)")
        c.executemany("INSERT INTO disposable VALUES (?)", [(b"x" * 4000,)] * 100)
    with db.transaction() as c:
        c.execute("DELETE FROM disposable")
    before = db.fetchone("PRAGMA freelist_count")[0]
    status = events_store.bounded_maintenance()
    assert status.last_error is None
    assert status.pages_reclaimed > 0
    assert db.fetchone("PRAGMA freelist_count")[0] < before


def test_space_failure_is_durable_visible_and_retryable(legacy, monkeypatch):
    statements = []
    original_connect = db.sqlite3.connect

    def traced_connect(*args, **kwargs):
        c = original_connect(*args, **kwargs)
        c.set_trace_callback(statements.append)
        return c

    monkeypatch.setattr(db.sqlite3, "connect", traced_connect)
    monkeypatch.setattr(
        db.shutil, "disk_usage",
        lambda path: SimpleNamespace(free=math.ceil(legacy.stat().st_size * 1.5) - 1),
    )
    with TestClient(app) as client:
        error = db.get_setting(db.COMPACTION_MIGRATION_KEY)["last_error"]
        assert error.startswith("compaction unavailable: needs ")
        assert error.endswith(" MB free")
        assert db.fetchone("PRAGMA auto_vacuum")[0] == 0
        assert "VACUUM" not in statements
        with original_connect(legacy) as observer:
            persisted = observer.execute(
                "SELECT value FROM settings WHERE key = ?", (db.COMPACTION_MIGRATION_KEY,)
            ).fetchone()[0]
        assert error in persisted
        assert not db.get_setting(db.COMPACTION_MIGRATION_KEY).get("migrated_at")
        for _ in range(2):
            assert events_store.bounded_maintenance().last_error == error
        status = client.get("/api/settings").json()["event_maintenance"]
        assert status["last_error"] == error
        assert status["migration_required"]
        response = client.post("/api/settings/events/compaction-migration")
        assert response.status_code == 200
        assert response.json()["last_error"] == error
        assert "VACUUM" not in statements
        monkeypatch.setattr(db.shutil, "disk_usage", lambda path: SimpleNamespace(free=10**12))
        response = client.post("/api/settings/events/compaction-migration")
        assert response.status_code == 200
        assert response.json()["last_error"] is None
        assert not response.json()["migration_required"]
        assert db.fetchone("PRAGMA auto_vacuum")[0] == 2
        assert statements.count("VACUUM") == 1
        client.post("/api/settings/events/compaction-migration")
        assert statements.count("VACUUM") == 1
    db.reset_for_tests(str(legacy))
    assert db.get_setting(db.COMPACTION_MIGRATION_KEY)["migrated_at"]
    assert db.fetchone("SELECT value FROM retained_data")[0] == "existing installation"


def test_migration_holds_writer_lock(legacy, monkeypatch):
    monkeypatch.setattr(db.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    db.connect()
    migration_waiting = threading.Event()
    release_migration = threading.Event()
    writer_started = threading.Event()
    writer_done = threading.Event()

    def available(path):
        migration_waiting.set()
        assert release_migration.wait(5)
        return SimpleNamespace(free=10**12)

    def write():
        writer_started.set()
        db.set_setting("concurrent_writer", True)
        writer_done.set()

    monkeypatch.setattr(db.shutil, "disk_usage", available)
    migration = threading.Thread(target=db.migrate_compaction)
    writer = threading.Thread(target=write)
    migration.start()
    try:
        assert migration_waiting.wait(5)
        writer.start()
        assert writer_started.wait(5)
        assert not writer_done.wait(0.1)
    finally:
        release_migration.set()
        migration.join(5)
        if writer.ident is not None:
            writer.join(5)
    assert not migration.is_alive()
    assert writer_done.is_set()
    assert db.get_setting("concurrent_writer") is True
    assert db.fetchone("PRAGMA auto_vacuum")[0] == 2


def test_full_auto_vacuum_is_not_rewritten(legacy, monkeypatch):
    with sqlite3.connect(legacy) as c:
        c.execute("PRAGMA auto_vacuum=FULL")
        c.execute("VACUUM")

    def unexpected_space_check(path):
        pytest.fail("Only NONE mode may enter the compaction migration")

    monkeypatch.setattr(db.shutil, "disk_usage", unexpected_space_check)
    db.connect()
    assert db.fetchone("PRAGMA auto_vacuum")[0] == 1
    assert "not in incremental mode" in events_store.maintenance_status().last_error
