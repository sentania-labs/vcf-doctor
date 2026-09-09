"""The one-shot SQLite import: a lab upgrading from the old volume keeps its
history, including snapshots written before the gzip change."""

import gzip
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import db, import_sqlite
from app.collectors.fixture import load_fixture
from app.events import store as events_store
from app.snapshots import store

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

# The shipped SQLite schema, as an upgrading deployment's volume holds it:
# integer flags, a NOT NULL resources text column and no resources_gz.
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
CREATE TABLE scan_runs (
    id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, started TEXT NOT NULL, finished TEXT,
    status TEXT NOT NULL, error TEXT, snapshot_id TEXT, trigger TEXT NOT NULL DEFAULT 'manual'
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
CREATE TABLE changes (
    id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, from_snapshot_id TEXT NOT NULL,
    to_snapshot_id TEXT NOT NULL, observed_at TEXT NOT NULL, resource_id TEXT NOT NULL,
    resource_type TEXT NOT NULL, resource_name TEXT NOT NULL, change_type TEXT NOT NULL,
    significance TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
    property_changes TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE events (
    id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, time TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'event', type TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'info', message TEXT NOT NULL DEFAULT '',
    user TEXT, resource_id TEXT, resource_name TEXT, resource_type TEXT
);
CREATE TABLE event_capture_state (
    connection_id TEXT PRIMARY KEY, last_complete_end TEXT,
    task_history_unavailable INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE event_incomplete_intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, connection_id TEXT NOT NULL, since TEXT NOT NULL,
    until TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1, last_error TEXT,
    updated_at TEXT NOT NULL, UNIQUE(connection_id, since, until)
);
CREATE TABLE event_maintenance (
    id INTEGER PRIMARY KEY CHECK (id = 1), last_run TEXT, last_error TEXT,
    pages_reclaimed INTEGER NOT NULL DEFAULT 0
);
"""


@pytest.fixture(autouse=True)
def _fresh_db():
    db.reset_for_tests()


def _legacy_db(path: Path, *, snapshot_age: timedelta = timedelta(0)) -> str:
    resources = load_fixture("snapshot_a.json")
    payload = json.dumps([r.model_dump(mode="json") for r in resources])
    raw = sqlite3.connect(path)
    raw.executescript(LEGACY_SCHEMA)
    raw.execute(
        "INSERT INTO connections VALUES('legacy1','Lab WLD','vc01','svc','encrypted',1,"
        "'vcenter',?)",
        (NOW.isoformat(),),
    )
    raw.execute(
        "INSERT INTO schedules(connection_id, interval_minutes, enabled, last_status) "
        "VALUES('legacy1', 30, 1, 'ok')"
    )
    raw.execute(
        "INSERT INTO scan_runs VALUES('run1','legacy1',?,?,'ok',NULL,'snap1','scheduled')",
        (NOW.isoformat(), NOW.isoformat()),
    )
    raw.execute(
        "INSERT INTO snapshots VALUES('snap1','legacy1',?,'Scheduled',1,?,?)",
        ((NOW - snapshot_age).isoformat(), len(resources), payload),
    )
    raw.execute("INSERT INTO findings VALUES('snap1', ?)", (json.dumps([]),))
    raw.execute(
        "INSERT INTO changes VALUES('chg1','legacy1','snap0','snap1',?,'host:legacy1:h1',"
        "'host','esx01','modified','high','Host disconnected','{}')",
        (NOW.isoformat(),),
    )
    raw.execute(
        "INSERT INTO events VALUES('legacy1:1','legacy1',?,'event','VmPoweredOffEvent','user',"
        "'vm1 powered off','ops@vsphere.local','vm:legacy1:v1','vm1','vm')",
        (NOW.isoformat(),),
    )
    raw.execute(
        "INSERT INTO event_capture_state VALUES('legacy1', ?, 1)", (NOW.isoformat(),)
    )
    raw.execute(
        "INSERT INTO event_incomplete_intervals(id, connection_id, since, until, attempts, "
        "last_error, updated_at) VALUES(4, 'legacy1', ?, ?, 2, 'timeout', ?)",
        ((NOW - timedelta(hours=1)).isoformat(), NOW.isoformat(), NOW.isoformat()),
    )
    raw.execute("INSERT INTO event_maintenance(id, pages_reclaimed) VALUES(1, 42)")
    raw.execute(
        "INSERT INTO settings VALUES('retention_policy', ?)",
        (json.dumps({"recent_days": 7, "hourly_days": 20, "daily_days": 90, "timezone": "UTC"}),),
    )
    raw.execute(
        "INSERT INTO settings VALUES('incremental_compaction_migration', ?)",
        (json.dumps({"migrated_at": NOW.isoformat(), "last_error": None}),),
    )
    raw.commit()
    raw.close()
    return "legacy1"


def test_import_carries_the_whole_history_across(tmp_path):
    path = tmp_path / "vcf-doctor.db"
    cid = _legacy_db(path)

    moved = import_sqlite.run(path)

    assert moved["connections"] == 1
    assert moved["snapshots"] == 1
    assert moved["events"] == 1
    # The compaction bookkeeping row is dropped; the retention policy comes over.
    assert moved["settings"] == 1
    assert db.get_setting("incremental_compaction_migration") is None
    assert db.get_setting("retention_policy")["recent_days"] == 7

    conn = store.get_connection(cid)
    assert conn is not None and conn.name == "Lab WLD"
    assert conn.verify_tls is True  # integer 1 became a real boolean
    assert store.get_schedule(cid).enabled is True
    assert store.get_run("run1").status == "ok"
    assert store.get_findings("snap1") == []
    assert [c.summary for c in store.list_change_log(cid)] == ["Host disconnected"]

    events = events_store.list_events(cid, since=NOW - timedelta(days=1))
    assert [e.user for e in events] == ["ops@vsphere.local"]
    status = events_store.capture_status(cid)
    assert status.task_history_unavailable is True
    assert status.last_complete_end == NOW
    assert [i.attempts for i in status.incomplete_intervals] == [2]


def test_legacy_text_snapshots_arrive_compressed_and_readable(tmp_path):
    path = tmp_path / "vcf-doctor.db"
    _legacy_db(path)
    import_sqlite.run(path)

    row = db.fetchone("SELECT resources_gz FROM snapshots WHERE id = 'snap1'")
    blob = bytes(row["resources_gz"])
    expected = load_fixture("snapshot_a.json")
    assert json.loads(gzip.decompress(blob)) == [r.model_dump(mode="json") for r in expected]
    assert store.get_snapshot("snap1").resources == expected


def test_import_refuses_a_target_that_already_holds_history(tmp_path):
    path = tmp_path / "vcf-doctor.db"
    _legacy_db(path)
    import_sqlite.run(path)
    with pytest.raises(SystemExit) as refused:
        import_sqlite.run(path)
    assert "already holds history" in str(refused.value)
    assert import_sqlite.run(path, force=True)["connections"] == 1  # nothing doubles
    assert db.fetchone("SELECT COUNT(*) AS n FROM connections")["n"] == 1


def test_imported_intervals_do_not_collide_with_the_next_generated_id(tmp_path):
    """The imported rows carry their old ids, so the identity has to be moved
    past them or the next recorded gap fails on a duplicate key."""
    path = tmp_path / "vcf-doctor.db"
    cid = _legacy_db(path)
    import_sqlite.run(path)
    events_store.record_incomplete_interval(
        cid, NOW + timedelta(hours=2), NOW + timedelta(hours=3), "later"
    )
    ids = [i.id for i in events_store.capture_status(cid).incomplete_intervals]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_settings_the_console_seeded_do_not_block_an_import(tmp_path):
    """The console writes a session secret, an operator password and the default
    event policy on its first start, so a target is never literally empty. Those
    are not history, and the upgrading deployment's own settings replace them."""
    db.set_setting("auth_password", "the-fresh-install-password")
    db.set_setting("session_secret", "fresh")
    path = tmp_path / "vcf-doctor.db"
    raw = sqlite3.connect(path)
    raw.executescript(LEGACY_SCHEMA)
    raw.execute(
        "INSERT INTO settings VALUES('auth_password', ?)", (json.dumps("the-old-password"),)
    )
    raw.commit()
    raw.close()

    import_sqlite.run(path)

    assert db.get_setting("auth_password") == "the-old-password"
    assert db.get_setting("session_secret") == "fresh"  # not in the old database


def test_a_column_the_old_volume_never_had_takes_the_target_default(tmp_path):
    """Columns were added to the SQLite schema over time, so an older volume is
    missing some of them, and four of the targets are NOT NULL. A column the
    source never had is left out of the INSERT so the target's default applies,
    rather than failing partway through a batched import."""
    path = tmp_path / "vcf-doctor.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE connections (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL,
            username TEXT NOT NULL, password TEXT NOT NULL,
            verify_tls INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE event_capture_state (
            connection_id TEXT PRIMARY KEY, last_complete_end TEXT
        );
        """
    )
    raw.execute(
        "INSERT INTO connections VALUES('legacy1','Lab WLD','vc01','svc','encrypted',1,?)",
        (NOW.isoformat(),),
    )
    raw.execute("INSERT INTO event_capture_state VALUES('legacy1', ?)", (NOW.isoformat(),))
    raw.commit()
    raw.close()

    moved = import_sqlite.run(path)

    assert moved["connections"] == 1 and moved["event_capture_state"] == 1
    assert store.get_connection("legacy1").kind == "vcenter"
    status = events_store.capture_status("legacy1")
    assert status.task_history_unavailable is False
    assert status.last_complete_end == NOW


def test_missing_file_is_named_not_a_traceback(tmp_path):
    with pytest.raises(FileNotFoundError) as missing:
        import_sqlite.run(tmp_path / "not-here.db")
    assert "not-here.db" in str(missing.value)
