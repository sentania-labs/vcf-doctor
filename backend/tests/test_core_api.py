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
            "health_score",
            "counts",
            "resource_counts",
            "hosts_connected",
            "hosts_total",
            "vms_on",
            "vms_total",
            "storage_free_pct",
            "last_scan",
            "top_findings",
            "recent_changes",
        ):
            assert key in o, key
        assert set(o["counts"]) == {"critical", "warning", "info", "passed"}
        assert o["hosts_total"] >= 1 and o["vms_total"] >= 1
        assert o["last_run"]["status"] == "ok"
        assert isinstance(o["last_scan"], str)
        assert 0 <= o["health_score"] <= 100


def test_settings_retention_policy_roundtrip(client):
    body = client.get("/api/settings").json()
    assert "retention" not in body
    assert body["retention_policy"] == {"recent_days": 14, "hourly_days": 30, "daily_days": 365}
    r = client.put("/api/settings", json={"retention_policy": {"recent_days": 7}})
    assert r.status_code == 200, r.text
    assert r.json()["retention_policy"] == {"recent_days": 7, "hourly_days": 30, "daily_days": 365}
    assert client.get("/api/settings").json()["retention_policy"]["recent_days"] == 7
    # The old count is neither accepted nor echoed.
    r = client.put("/api/settings", json={"retention": 12})
    assert r.status_code == 200 and "retention" not in r.json()


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


def test_fixture_kind_never_hijacks_live_connections():
    """A vcenter-kind connection always gets the vSphere collector."""
    from datetime import UTC, datetime

    from app.collectors import registry
    from app.collectors.fixture import FixtureCollector
    from app.models import Connection

    live = Connection(
        id="c1",
        name="wld",
        host="vc.example",
        username="u",
        password="p",
        created_at=datetime.now(UTC),
        kind="vcenter",
    )
    assert not isinstance(registry.get_collector(live), FixtureCollector)
    fixture = live.model_copy(update={"kind": "fixture"})
    assert isinstance(registry.get_collector(fixture), FixtureCollector)


def test_fixture_hook_is_off_by_default(monkeypatch):
    """A clean environment never enables the fixture collector."""
    from app.config import Settings

    monkeypatch.delenv("VCF_DOCTOR_TEST_FIXTURES", raising=False)
    assert Settings().test_fixtures is False


def test_fixture_kind_is_test_only(client, monkeypatch):
    """Without the VCF_DOCTOR_TEST_FIXTURES hook (the production default) a
    fixture connection cannot be created, switched to, tested, or scanned."""
    from app.collectors import registry
    from app.config import settings
    from app.snapshots import store

    live = {**FIXTURE_CONN, "kind": "vcenter", "host": "vc.example"}
    cid = client.post("/api/connections", json=live).json()["id"]
    monkeypatch.setattr(settings, "test_fixtures", False)
    r = client.post("/api/connections", json=FIXTURE_CONN)
    assert r.status_code == 400 and "kind" in r.json()["detail"]
    assert client.put(f"/api/connections/{cid}", json={"kind": "fixture"}).status_code == 400
    assert client.post("/api/connections/test", json=FIXTURE_CONN).status_code == 400
    assert client.get(f"/api/connections/{cid}").json()["kind"] == "vcenter"
    # A leftover fixture connection in the database errors instead of scanning.
    leftover = store.create_connection(ConnectionCreate(**FIXTURE_CONN))
    with pytest.raises(registry.CollectorUnavailable):
        registry.get_collector(leftover)
    assert client.post(f"/api/connections/{leftover.id}/test").status_code == 503
    run = client.post("/api/scan", json={"connection_id": leftover.id}).json()[0]
    assert run["status"] == "error" and "tests only" in run["error"]
    # Plain unknown kinds are refused too.
    r = client.post("/api/connections", json={**live, "kind": "nsx"})
    assert r.status_code == 400


def test_settings_carries_assistant_and_never_echoes_key(client):
    r = client.put(
        "/api/settings",
        json={
            "retention_policy": {"recent_days": 5},
            "assistant": {"provider": "mock", "api_key": "sk-secret"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["retention_policy"]["recent_days"] == 5
    assert body["assistant"]["provider"] == "mock"
    assert body["assistant"]["api_key_set"] is True
    assert "sk-secret" not in r.text


# --- changes_min_significance setting and ?min_significance= ---------------


def test_settings_changes_min_significance_roundtrip_and_validation(client):
    assert client.get("/api/settings").json()["changes_min_significance"] == "low"
    r = client.put("/api/settings", json={"changes_min_significance": "medium"})
    assert r.status_code == 200 and r.json()["changes_min_significance"] == "medium"
    assert client.get("/api/settings").json()["changes_min_significance"] == "medium"
    r = client.put("/api/settings", json={"changes_min_significance": "urgent"})
    assert r.status_code == 400
    assert client.get("/api/settings").json()["changes_min_significance"] == "medium"
    # A partial update that omits the key leaves it alone.
    client.put("/api/settings", json={"retention_policy": {"recent_days": 5}})
    assert client.get("/api/settings").json()["changes_min_significance"] == "medium"


def test_changes_min_significance_param_and_setting(client, monkeypatch):
    from app.models import Change

    cid = _add(client)
    client.post("/api/scan", json={"connection_id": cid})
    client.post("/api/scan", json={"connection_id": cid})
    assert len(client.get(f"/api/snapshots?connection_id={cid}").json()) == 2

    def fake_changes(old, new):
        return [
            Change(
                change_type="modified",
                resource_id="host:x:a",
                resource_type="host",
                resource_name="a",
                significance="high",
                summary="high one",
            ),
            Change(
                change_type="modified",
                resource_id="vm:x:b",
                resource_type="vm",
                resource_name="b",
                significance="medium",
                summary="medium one",
            ),
            Change(
                change_type="added",
                resource_id="vm:x:c",
                resource_type="vm",
                resource_name="c",
                significance="low",
                summary="low one",
            ),
        ]

    monkeypatch.setattr(scheduler, "compute_changes", fake_changes)
    base = f"/api/changes?connection_id={cid}"
    assert [c["significance"] for c in client.get(base).json()] == ["high", "medium", "low"]
    assert [c["significance"] for c in client.get(base + "&min_significance=medium").json()] == [
        "high",
        "medium",
    ]
    assert [c["significance"] for c in client.get(base + "&min_significance=high").json()] == [
        "high",
    ]
    assert client.get(base + "&min_significance=bogus").status_code == 400

    # The setting is the default when no param is given; an explicit param overrides it.
    client.put("/api/settings", json={"changes_min_significance": "high"})
    assert [c["significance"] for c in client.get(base).json()] == ["high"]
    assert [c["significance"] for c in client.get(base + "&min_significance=low").json()] == [
        "high",
        "medium",
        "low",
    ]

    # Overview recent_changes honours the same floor. It reads the persisted
    # log written by the two real scans above (the fake diff is not consulted).
    ov = client.get(f"/api/overview?connection_id={cid}").json()
    assert ov["recent_changes"] and all(c["significance"] == "high" for c in ov["recent_changes"])
    ov = client.get(f"/api/overview?connection_id={cid}&min_significance=low").json()
    assert len(ov["recent_changes"]) == 5
    assert ov["recent_changes"][0]["significance"] == "high"
    assert client.get(f"/api/overview?connection_id={cid}&min_significance=nope").status_code == 400


def test_changes_min_significance_against_real_fixture_scans(client):
    cid = _add(client)
    client.post("/api/scan", json={"connection_id": cid})
    client.post("/api/scan", json={"connection_id": cid})
    everything = client.get(f"/api/changes?connection_id={cid}").json()
    high_only = client.get(f"/api/changes?connection_id={cid}&min_significance=high").json()
    assert everything, "fixture A -> B should produce changes"
    assert high_only and all(c["significance"] == "high" for c in high_only)
    assert len(high_only) == sum(1 for c in everything if c["significance"] == "high")
    for c in everything:
        assert set(c) >= {
            "change_type",
            "resource_id",
            "significance",
            "summary",
            "property_changes",
        }
