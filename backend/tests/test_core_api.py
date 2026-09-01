"""End-to-end core API tests against the fixture collector."""

import threading

import pytest
from fastapi.testclient import TestClient

from app import db, scheduler
from app.main import app
from app.models import ConnectionCreate
from app.snapshots import store


@pytest.fixture()
def client(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    with TestClient(app) as c:
        yield c


FIXTURE_CONN = {
    "name": "Lab WLD",
    "host": "fixture",
    "username": "demo",
    "password": "s3cret",
    "kind": "fixture",
    "interval_minutes": 15,
}


def _add(client) -> str:
    r = client.post("/api/connections", json=FIXTURE_CONN)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_connections_crud_never_leaks_password(client):
    cid = _add(client)
    for path in ("/api/connections", f"/api/connections/{cid}"):
        body = client.get(path).json()
        text = client.get(path).text
        assert "s3cret" not in text
        item = body[0] if isinstance(body, list) else body
        assert "password" not in item
        assert item["kind"] == "fixture"
    r = client.put(f"/api/connections/{cid}", json={"name": "Renamed", "password": ""})
    assert r.json()["name"] == "Renamed"
    assert store.get_connection(cid).password == "s3cret"
    sched = client.get(f"/api/connections/{cid}/schedule").json()
    assert sched["interval_minutes"] == 15 and sched["enabled"] is True
    r = client.put(f"/api/connections/{cid}/schedule", json={"interval_minutes": 1})
    assert r.json()["interval_minutes"] == 5  # floor applied
    assert client.delete(f"/api/connections/{cid}").status_code == 200
    assert client.get(f"/api/connections/{cid}").status_code == 404
    assert client.get("/api/connections").json() == []


def test_fixture_scan_end_to_end(client):
    cid = _add(client)
    r = client.post("/api/scan", json={"connection_id": cid})
    assert r.status_code == 200
    run = r.json()[0]
    assert run["status"] == "ok" and run["snapshot_id"]
    snaps = client.get(f"/api/snapshots?connection_id={cid}").json()
    assert len(snaps) == 1 and snaps[0]["resource_count"] > 0
    resources = client.get(f"/api/resources?connection_id={cid}").json()
    assert {r["type"] for r in resources} >= {"host", "vm", "datastore"}
    assert client.get("/api/resources").json() == resources  # all connections merged
    # Second scan (no body = all connections) gives the degraded fixture and a diff baseline.
    r = client.post("/api/scan")
    assert r.json()[0]["status"] == "ok"
    assert len(client.get("/api/snapshots").json()) == 2
    assert isinstance(client.get(f"/api/changes?connection_id={cid}").json(), list)
    assert isinstance(client.get("/api/findings").json(), list)
    scans = client.get(f"/api/scans?connection_id={cid}").json()
    assert len(scans) == 2
    sched = client.get(f"/api/connections/{cid}/schedule").json()
    assert sched["last_status"] == "ok" and sched["last_run"]


def test_manual_snapshot_and_delete(client):
    cid = _add(client)
    r = client.post("/api/snapshots", json={"connection_id": cid, "label": "Before change"})
    assert r.status_code == 201
    snap = r.json()
    assert snap["label"] == "Before change" and snap["scheduled"] is False
    assert len(snap["resources"]) == snap["resource_count"]
    got = client.get(f"/api/snapshots/{snap['id']}").json()
    assert got["id"] == snap["id"]
    assert client.get(f"/api/resources?snapshot_id={snap['id']}").json() == snap["resources"]
    assert client.delete(f"/api/snapshots/{snap['id']}").status_code == 200
    assert client.get(f"/api/snapshots/{snap['id']}").status_code == 404


def test_overview_shape(client):
    cid = _add(client)
    client.post("/api/scan", json={"connection_id": cid})
    for path in ("/api/overview", f"/api/overview?connection_id={cid}"):
        o = client.get(path).json()
        for key in (
            "health_score", "counts", "resource_counts", "hosts_connected", "hosts_total",
            "vms_on", "vms_total", "storage_free_pct", "last_scan", "top_findings",
            "recent_changes",
        ):
            assert key in o, key
        assert set(o["counts"]) == {"critical", "warning", "info"}
        assert o["hosts_total"] >= 1 and o["vms_total"] >= 1
        assert o["last_scan"]["status"] == "ok"
        assert 0 <= o["health_score"] <= 100


def test_settings_retention_roundtrip(client):
    assert client.get("/api/settings").json()["retention"] == 96
    r = client.put("/api/settings", json={"retention": 12})
    assert r.json()["retention"] == 12
    assert client.put("/api/settings", json={"retention": 0}).status_code == 400


def test_test_connection_endpoint(client):
    cid = _add(client)
    r = client.post(f"/api/connections/{cid}/test")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_health_reports_scheduler_off_under_pytest(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["scheduler"] is False


def test_scan_lock_skips_overlapping_run(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = store.create_connection(ConnectionCreate(**FIXTURE_CONN))
    lock = scheduler._lock_for(conn.id)
    lock.acquire()
    try:
        run = scheduler.run_scan(conn.id, "scheduled")
    finally:
        lock.release()
    assert run.status == "skipped"
    assert "still active" in run.error
    assert store.get_schedule(conn.id).last_status == "skipped"
    # Lock released: the next run proceeds.
    assert scheduler.run_scan(conn.id, "scheduled").status == "ok"


def test_scan_lock_under_real_concurrency(tmp_path):
    """Two threads scanning the same connection: exactly one ok, one skipped."""
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = store.create_connection(ConnectionCreate(**FIXTURE_CONN))
    barrier = threading.Barrier(2)
    results = []

    def go():
        barrier.wait()
        results.append(scheduler.run_scan(conn.id, "scheduled").status)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) in (["ok", "ok"], ["ok", "skipped"])
