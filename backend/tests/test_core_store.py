"""Persistence and retention tests for the snapshot store."""

from datetime import timedelta

from app import db, scheduler
from app.models import ConnectionCreate, Finding, Resource
from app.models.snapshot import RetentionPolicy
from app.snapshots import store


def _conn():
    return store.create_connection(
        ConnectionCreate(name="c", host="fixture", username="u", password="p", kind="fixture")
    )


def _res(i: int) -> Resource:
    return Resource(
        id=f"host:vc:esx{i}",
        type="host",
        name=f"esx{i}",
        source="vcenter:vc",
        properties={"connectionState": "connected"},
    )


def test_snapshot_persistence_roundtrip(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    snap = store.save_snapshot(conn.id, [_res(1), _res(2)], "Manual x", scheduled=False)
    loaded = store.get_snapshot(snap.id)
    assert loaded.resources == snap.resources
    assert loaded.resource_count == 2 and loaded.scheduled is False
    assert [s.id for s in store.list_snapshots(conn.id)] == [snap.id]
    f = Finding(id="f1", check_id="x", severity="warning", title="t", summary="s")
    store.save_findings(snap.id, [f])
    assert store.get_findings(snap.id) == [f]
    assert store.latest_snapshot(conn.id).id == snap.id
    assert store.delete_snapshot(snap.id) is True
    assert store.get_findings(snap.id) == []


def test_retention_prunes_only_scheduled(tmp_path):
    """Tier pruning never touches manual snapshots, even ancient ones."""
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    manual = store.save_snapshot(conn.id, [_res(1)], "keep me", scheduled=False)
    old = store.save_snapshot(conn.id, [_res(1)], "Scheduled ancient", scheduled=True)
    fresh = store.save_snapshot(conn.id, [_res(1)], "Scheduled fresh", scheduled=True)
    ancient = (store.now() - timedelta(days=400)).isoformat()
    with db.transaction() as c:
        c.execute(
            "UPDATE snapshots SET created_at = ? WHERE id IN (?, ?)", (ancient, manual.id, old.id)
        )
    assert store.apply_retention(conn.id) == 1
    remaining = {s.id: s.tier for s in store.list_snapshots(conn.id)}
    assert remaining == {manual.id: "manual", fresh.id: "recent"}


def test_scan_applies_retention_policy_setting(tmp_path):
    """run_scan applies the stored policy after every scan, manual or scheduled."""
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    assert scheduler.run_scan(conn.id, "scheduled").status == "ok"
    stale = (store.now() - timedelta(days=3)).isoformat()
    with db.transaction() as c:
        c.execute("UPDATE snapshots SET created_at = ? WHERE connection_id = ?", (stale, conn.id))
    # Default policy keeps a 3-day-old scheduled snapshot (recent tier).
    assert scheduler.run_scan(conn.id, "manual").status == "ok"
    assert len(store.list_snapshots(conn.id)) == 2
    # Shrinking the tiers below its age prunes it on the next scan.
    store.set_retention_policy(RetentionPolicy(recent_days=1, hourly_days=1, daily_days=1))
    assert scheduler.run_scan(conn.id, "scheduled").status == "ok"
    snaps = store.list_snapshots(conn.id)
    assert len(snaps) == 2  # stale scheduled one gone; the manual and the new one remain
    assert {s.tier for s in snaps} == {"manual", "recent"}
    assert snaps[0].label.startswith("Scheduled ")


def test_snapshots_are_keyed_per_connection(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    a, b = _conn(), _conn()
    store.save_snapshot(a.id, [_res(1)], "a", scheduled=True)
    store.save_snapshot(b.id, [_res(2)], "b", scheduled=True)
    assert len(store.list_snapshots(a.id)) == 1
    assert len(store.list_snapshots()) == 2
    store.delete_connection(a.id)
    assert store.list_snapshots(a.id) == []
    assert len(store.list_snapshots(b.id)) == 1
