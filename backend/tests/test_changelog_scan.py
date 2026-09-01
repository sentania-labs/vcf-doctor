"""run_scan persists diff(previous, current) into the changes table."""

from datetime import timedelta

from app import db, scheduler
from app.models import ConnectionCreate
from app.models.snapshot import RetentionPolicy
from app.snapshots import store


def _conn():
    return store.create_connection(
        ConnectionCreate(name="c", host="fixture", username="u", password="p", kind="fixture")
    )


def test_fixture_scans_write_fifteen_rows_a_to_b(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    first = scheduler.run_scan(conn.id, "manual")
    assert first.status == "ok"
    assert store.count_changes(conn.id) == 0  # nothing to compare against yet
    second = scheduler.run_scan(conn.id, "scheduled")
    assert second.status == "ok"
    assert store.count_changes(conn.id) == 15

    rows = store.list_change_log(conn.id, since=store.now() - timedelta(hours=1), limit=1000)
    assert len(rows) == 15
    to_snap = store.get_snapshot(second.snapshot_id)
    assert {r.from_snapshot_id for r in rows} == {first.snapshot_id}
    assert {r.to_snapshot_id for r in rows} == {second.snapshot_id}
    assert {r.observed_at for r in rows} == {to_snap.created_at}
    assert {r.connection_id for r in rows} == {conn.id}
    # All significances are persisted; filtering is a read concern.
    assert {r.significance for r in rows} == {"high", "medium", "low"}
    # property_changes round-trip as {old, new}.
    esx03 = next(r for r in rows if r.resource_name.startswith("esx03"))
    assert esx03.property_changes["connectionState"].old == "connected"
    assert esx03.property_changes["connectionState"].new == "disconnected"
    assert esx03.summary == "connectionState connected -> disconnected"
    # Newest first, high significance first within one observation.
    assert [r.significance for r in rows[:4]] == ["high"] * 4

    # B -> B: a scan with no change adds no rows.
    assert scheduler.run_scan(conn.id, "scheduled").status == "ok"
    assert store.count_changes(conn.id) == 15


def test_change_rows_outlive_pruned_snapshots_and_expire_with_daily_days(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    scheduler.run_scan(conn.id, "scheduled")
    second = scheduler.run_scan(conn.id, "scheduled")
    assert store.count_changes(conn.id) == 15

    # Age both snapshots past a tiny policy: snapshots go, rows stay.
    old = (store.now() - timedelta(days=5)).isoformat()
    with db.transaction() as c:
        c.execute("UPDATE snapshots SET created_at = ? WHERE connection_id = ?", (old, conn.id))
    tiny = RetentionPolicy(recent_days=1, hourly_days=1, daily_days=2)
    assert store.apply_retention(conn.id, tiny) == 2
    assert store.list_snapshots(conn.id) == []
    assert store.count_changes(conn.id) == 15
    assert store.get_snapshot(second.snapshot_id) is None

    # Once observed_at is older than daily_days the rows expire too.
    with db.transaction() as c:
        c.execute("UPDATE changes SET observed_at = ? WHERE connection_id = ?", (old, conn.id))
    store.apply_retention(conn.id, RetentionPolicy(recent_days=1, hourly_days=1, daily_days=1))
    assert store.count_changes(conn.id) == 0


def test_deleting_a_connection_removes_its_change_rows(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn, other = _conn(), _conn()
    for c in (conn, other):
        scheduler.run_scan(c.id, "manual")
        scheduler.run_scan(c.id, "manual")
    assert store.count_changes(conn.id) == store.count_changes(other.id) == 15
    store.delete_connection(conn.id)
    assert store.count_changes(conn.id) == 0
    assert store.count_changes(other.id) == 15
