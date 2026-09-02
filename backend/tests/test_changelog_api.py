"""GET /api/changes/log filters and the overview's use of the persisted log."""

from datetime import timedelta
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import db
from app.collectors.fixture import load_fixture, namespace_resources
from app.main import app
from app.snapshots import store

FIXTURE_CONN = {
    "name": "Lab WLD",
    "host": "fixture",
    "username": "demo",
    "password": "s3cret",
    "kind": "fixture",
    "interval_minutes": 15,
}


@pytest.fixture()
def client(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    with TestClient(app) as c:
        yield c


def _scanned_connection(client) -> str:
    cid = client.post("/api/connections", json=FIXTURE_CONN).json()["id"]
    for _ in range(2):
        assert client.post("/api/scan", json={"connection_id": cid}).json()[0]["status"] == "ok"
    return cid


def test_log_defaults_newest_first_last_24h(client):
    cid = _scanned_connection(client)
    rows = client.get(f"/api/changes/log?connection_id={cid}").json()
    assert len(rows) == 15
    stamps = [r["observed_at"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)
    assert [r["significance"] for r in rows[:4]] == ["high"] * 4
    assert set(rows[0]) >= {
        "id",
        "connection_id",
        "from_snapshot_id",
        "to_snapshot_id",
        "observed_at",
        "resource_id",
        "resource_type",
        "resource_name",
        "change_type",
        "significance",
        "summary",
        "property_changes",
    }
    # Rows observed more than 24 h ago fall outside the default window.
    old = (store.now() - timedelta(hours=25)).isoformat()
    with db.transaction() as c:
        c.execute("UPDATE changes SET observed_at = ? WHERE significance = 'low'", (old,))
    assert len(client.get(f"/api/changes/log?connection_id={cid}").json()) == 13
    since = quote((store.now() - timedelta(days=2)).isoformat())
    assert len(client.get(f"/api/changes/log?connection_id={cid}&since={since}").json()) == 15
    until = quote((store.now() - timedelta(hours=24)).isoformat())
    r = client.get(f"/api/changes/log?connection_id={cid}&since={since}&until={until}")
    assert [x["significance"] for x in r.json()] == ["low", "low"]
    # Without a connection the log spans every connection.
    assert len(client.get("/api/changes/log").json()) == 13


def test_log_filters_and_validation(client):
    cid = _scanned_connection(client)
    base = f"/api/changes/log?connection_id={cid}"
    assert len(client.get(base + "&min_significance=high").json()) == 4
    assert len(client.get(base + "&min_significance=medium").json()) == 13
    assert len(client.get(base + "&limit=3").json()) == 3
    rid = client.get(base + "&min_significance=high").json()[0]["resource_id"]
    only = client.get(base + f"&resource_id={rid}").json()
    assert only and all(r["resource_id"] == rid for r in only)

    # The setting is the default floor; an explicit param overrides it.
    client.put("/api/settings", json={"changes_min_significance": "high"})
    assert len(client.get(base).json()) == 4
    assert len(client.get(base + "&min_significance=low").json()) == 15

    assert client.get(base + "&min_significance=bogus").status_code == 400
    assert client.get(base + "&since=yesterday").status_code == 400
    assert (
        client.get(base + "&since=2030-01-01T00:00:00Z&until=2020-01-01T00:00:00Z").status_code
        == 400
    )
    assert client.get(base + "&limit=0").status_code == 422
    assert client.get("/api/changes/log?connection_id=nope").status_code == 404
    # ISO with a trailing Z is accepted, and so is an unencoded "+00:00"
    # offset (the "+" arrives as a space).
    assert client.get(base + "&since=2020-01-01T00:00:00Z").status_code == 200
    assert client.get(base + "&since=2020-01-01T00:00:00+00:00").status_code == 200


def test_on_demand_compare_endpoint_unchanged(client):
    cid = _scanned_connection(client)
    snaps = client.get(f"/api/snapshots?connection_id={cid}").json()
    assert [s["tier"] for s in snaps] == ["manual", "manual"]
    r = client.get(f"/api/changes?from={snaps[1]['id']}&to={snaps[0]['id']}")
    assert r.status_code == 200 and len(r.json()) == 15


def test_overview_reads_log_and_falls_back_when_empty(client):
    cid = client.post("/api/connections", json=FIXTURE_CONN).json()["id"]
    # Two snapshots written without a scan: no log rows, like a database from
    # before the change log existed. The overview falls back to a live diff.
    a = namespace_resources(load_fixture("snapshot_a.json"), cid)
    b = namespace_resources(load_fixture("snapshot_b.json"), cid)
    store.save_snapshot(cid, a, "A", scheduled=True)
    store.save_snapshot(cid, b, "B", scheduled=True)
    assert store.count_changes(cid) == 0
    ov = client.get(f"/api/overview?connection_id={cid}").json()
    assert len(ov["recent_changes"]) == 5
    assert ov["recent_changes"][0]["significance"] == "high"

    # A real scan (B -> B, no changes) writes no rows either; still falling back.
    client.post("/api/scan", json={"connection_id": cid})
    assert store.count_changes(cid) == 0

    # Seed a log row and the overview switches to the persisted log.
    from app.models import Change

    seeded = Change(
        change_type="modified",
        resource_id="host:x",
        resource_type="host",
        resource_name="seeded",
        significance="medium",
        summary="seeded row",
    )
    latest = store.latest_snapshots(cid, 2)
    store.save_changes(cid, latest[1].id, latest[0].id, latest[0].created_at, [seeded])
    ov = client.get(f"/api/overview?connection_id={cid}").json()
    assert [c["summary"] for c in ov["recent_changes"]] == ["seeded row"]
    assert (
        client.get(f"/api/overview?connection_id={cid}&min_significance=high").json()[
            "recent_changes"
        ]
        == []
    )


def test_overview_keeps_latest_pair_when_last_scan_is_older_than_24h(client):
    cid = _scanned_connection(client)
    assert store.count_changes(cid) == 15
    old = (store.now() - timedelta(hours=25)).isoformat()
    with db.transaction() as c:
        c.execute("UPDATE changes SET observed_at = ? WHERE connection_id = ?", (old, cid))
    # The default log window no longer covers the rows...
    assert client.get(f"/api/changes/log?connection_id={cid}").json() == []
    # ...but the Overview still shows the latest snapshot pair's changes.
    # The Overview feed is capped at 5 rows, high significance first.
    ov = client.get(f"/api/overview?connection_id={cid}").json()
    assert len(ov["recent_changes"]) == 5
    assert [c["significance"] for c in ov["recent_changes"]] == ["high"] * 4 + ["medium"]
    assert {c["observed_at"].replace("Z", "+00:00") for c in ov["recent_changes"]} == {old}
    high = client.get(f"/api/overview?connection_id={cid}&min_significance=high").json()
    assert [c["significance"] for c in high["recent_changes"]] == ["high"] * 4
    # The unscoped Overview (all connections) behaves the same.
    assert len(client.get("/api/overview").json()["recent_changes"]) == 5
