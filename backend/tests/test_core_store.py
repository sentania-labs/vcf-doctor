"""Persistence and retention tests for the snapshot store."""

from app import db, scheduler
from app.models import ConnectionCreate, Finding, Resource
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
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    manual = store.save_snapshot(conn.id, [_res(1)], "keep me", scheduled=False)
    scheduled_ids = [
        store.save_snapshot(conn.id, [_res(1)], f"Scheduled {i}", scheduled=True).id
        for i in range(5)
    ]
    removed = store.prune_scheduled(conn.id, keep=2)
    assert removed == 3
    remaining = {s.id for s in store.list_snapshots(conn.id)}
    assert manual.id in remaining
    assert set(scheduled_ids[-2:]) <= remaining
    assert not (set(scheduled_ids[:3]) & remaining)


def test_scheduled_scan_applies_retention_setting(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    db.set_setting("retention", 2)
    conn = _conn()
    for _ in range(4):
        assert scheduler.run_scan(conn.id, "scheduled").status == "ok"
    snaps = store.list_snapshots(conn.id)
    assert len(snaps) == 2 and all(s.scheduled for s in snaps)
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
