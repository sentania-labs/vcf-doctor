"""Gzip storage of snapshot resources and migration of legacy text rows."""

import gzip
import json
import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app import db
from app.collectors.fixture import load_fixture
from app.models import ConnectionCreate
from app.snapshots import store

LEGACY_SCHEMA = """
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE connections (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, username TEXT NOT NULL,
    password TEXT NOT NULL, verify_tls INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'vcenter', created_at TEXT NOT NULL
);
CREATE TABLE schedules (
    connection_id TEXT PRIMARY KEY REFERENCES connections(id) ON DELETE CASCADE,
    interval_minutes INTEGER NOT NULL DEFAULT 15, enabled INTEGER NOT NULL DEFAULT 1,
    last_run TEXT, next_run TEXT, last_status TEXT
);
CREATE TABLE snapshots (
    id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, created_at TEXT NOT NULL,
    label TEXT NOT NULL, scheduled INTEGER NOT NULL DEFAULT 0,
    resource_count INTEGER NOT NULL DEFAULT 0, resources TEXT NOT NULL
);
CREATE TABLE findings (
    snapshot_id TEXT PRIMARY KEY REFERENCES snapshots(id) ON DELETE CASCADE,
    findings TEXT NOT NULL
);
"""


def _conn():
    return store.create_connection(
        ConnectionCreate(name="c", host="fixture", username="u", password="p", kind="fixture")
    )


def _make_legacy_db(path: str, rows: int, age: timedelta = timedelta(0)) -> tuple[str, str]:
    """A pre-gzip database: NOT NULL text column, no resources_gz. Returns
    (connection_id, json payload) so callers can compare after migration."""
    resources = load_fixture("snapshot_a.json")
    payload = json.dumps([r.model_dump(mode="json") for r in resources])
    raw = sqlite3.connect(path)
    raw.executescript(LEGACY_SCHEMA)
    now = datetime.now(UTC)
    raw.execute(
        "INSERT INTO connections VALUES('legacy1','L','fixture','u','p',0,'fixture',?)",
        (now.isoformat(),),
    )
    raw.execute("INSERT INTO schedules(connection_id) VALUES('legacy1')")
    raw.executemany(
        "INSERT INTO snapshots VALUES(?,?,?,?,?,?,?)",
        [
            (
                f"legacy{i:04d}",
                "legacy1",
                (now - age - timedelta(minutes=i)).isoformat(),
                f"Scheduled {i}",
                1,
                len(resources),
                payload,
            )
            for i in range(rows)
        ],
    )
    raw.commit()
    raw.close()
    return "legacy1", payload


def test_gzip_roundtrip_and_ratio(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    resources = load_fixture("snapshot_a.json")
    snap = store.save_snapshot(conn.id, resources, "Manual", scheduled=False)
    row = db.fetchone("SELECT resources, resources_gz FROM snapshots WHERE id = ?", (snap.id,))
    assert row["resources"] is None  # fresh schema: the text column stays empty
    assert isinstance(row["resources_gz"], bytes)
    plain = len(json.dumps([r.model_dump(mode="json") for r in resources]).encode())
    assert plain / len(row["resources_gz"]) > 5, "expected at least 5x on the fixture"
    assert json.loads(gzip.decompress(row["resources_gz"])) == [
        r.model_dump(mode="json") for r in resources
    ]
    assert store.get_snapshot(snap.id).resources == resources


def test_legacy_text_rows_are_readable_then_migrated_in_batches(tmp_path, caplog):
    path = str(tmp_path / "legacy.db")
    cid, payload = _make_legacy_db(path, rows=450)
    db.reset_for_tests(path)
    db.connect()  # adds resources_gz via ALTER TABLE
    cols = {r["name"]: r for r in db.fetchall("PRAGMA table_info(snapshots)")}
    assert "resources_gz" in cols and cols["resources"]["notnull"] == 1

    # Fallback read path before migration.
    before = store.get_snapshot("legacy0007")
    assert before is not None and len(before.resources) == before.resource_count
    assert store.latest_snapshot(cid).id == "legacy0000"

    with caplog.at_level("INFO", logger="vcf_doctor.store"):
        assert store.migrate_legacy_snapshots(batch_size=200) == 450
    assert sum("compressed 200 legacy" in m for m in caplog.messages) == 2
    assert any("compressed 50 legacy" in m for m in caplog.messages)

    left = db.fetchone("SELECT COUNT(*) AS n FROM snapshots WHERE resources_gz IS NULL")["n"]
    assert left == 0
    # NOT NULL column in the legacy schema: emptied to '' rather than NULL.
    assert db.fetchone("SELECT DISTINCT resources FROM snapshots")["resources"] == ""
    assert store.get_snapshot("legacy0007").resources == before.resources
    assert store.migrate_legacy_snapshots(batch_size=200) == 0

    # New writes into the legacy schema also keep the NOT NULL column happy.
    snap = store.save_snapshot(cid, before.resources, "after migration", scheduled=False)
    assert store.get_snapshot(snap.id).resources == before.resources
    assert json.loads(payload)[0]["id"] == before.resources[0].id


def test_startup_migrates_and_applies_retention(tmp_path):
    """App startup compresses legacy rows and prunes per policy for every
    connection, so an old database is tidy before the first request."""
    from app.main import app

    path = str(tmp_path / "legacy.db")
    cid, _ = _make_legacy_db(path, rows=30, age=timedelta(days=400))
    db.reset_for_tests(path)
    with TestClient(app) as client:
        assert (
            db.fetchone("SELECT COUNT(*) AS n FROM snapshots WHERE resources_gz IS NULL")["n"] == 0
        )
        # 400 days old and scheduled: all beyond daily_days, all pruned.
        assert client.get(f"/api/snapshots?connection_id={cid}").json() == []
