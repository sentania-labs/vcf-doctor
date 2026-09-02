"""GET /api/environment/changes: the estate-wide roll-up over the persisted change log."""

from datetime import timedelta
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.snapshots import store


def _conn(name: str) -> dict:
    return {
        "name": name,
        "host": f"{name.lower()}.fixture",
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


def _scan(client, cid: str, times: int = 1) -> None:
    for _ in range(times):
        assert client.post("/api/scan", json={"connection_id": cid}).json()[0]["status"] == "ok"


def _shift(cid: str, delta: timedelta) -> None:
    """Move every snapshot and change row of a connection back in time."""
    snaps = db.fetchall("SELECT id, created_at FROM snapshots WHERE connection_id = ?", (cid,))
    with db.transaction() as c:
        for s in snaps:
            moved = (store._dt(s["created_at"]) + delta).isoformat()
            c.execute("UPDATE snapshots SET created_at = ? WHERE id = ?", (moved, s["id"]))
            c.execute(
                "UPDATE changes SET observed_at = ? WHERE to_snapshot_id = ?", (moved, s["id"])
            )


def _section(body: dict, cid: str) -> dict:
    return next(s for s in body["connections"] if s["connection_id"] == cid)


def test_empty_estate(client):
    body = client.get("/api/environment/changes").json()
    assert body["totals"] == {
        "connections": 0,
        "covered": 0,
        "no_data": 0,
        "changes": {"high": 0, "medium": 0, "low": 0, "total": 0},
        "findings_appeared": 0,
        "findings_cleared": 0,
        "findings_compared": 0,
    }
    assert body["connections"] == [] and body["window"] == "last_cycle"


def test_rollup_across_connections_default_last_cycle(client):
    a = client.post("/api/connections", json=_conn("Alpha")).json()["id"]
    b = client.post("/api/connections", json=_conn("Beta")).json()["id"]
    _scan(client, a, 2)
    _scan(client, b, 2)
    body = client.get("/api/environment/changes").json()
    assert body["window"] == "last_cycle"
    t = body["totals"]
    assert t["connections"] == 2 and t["covered"] == 2 and t["no_data"] == 0
    # The fixture's A -> B diff records 15 rows per connection, 4 of them high.
    assert t["changes"] == {"high": 8, "medium": 18, "low": 4, "total": 30}
    assert t["findings_appeared"] > 0
    # Sections are alphabetical by name and carry the same rows /changes/log returns.
    assert [s["name"] for s in body["connections"]] == ["Alpha", "Beta"]
    sec = _section(body, a)
    assert sec["has_data"] is True and sec["truncated"] is False
    assert sec["snapshots_in_window"] >= 1
    assert len(sec["changes"]) == 15 and sec["counts"]["total"] == 15
    log = client.get(f"/api/changes/log?connection_id={a}").json()
    assert [r["id"] for r in sec["changes"]] == [r["id"] for r in log]
    assert set(sec["changes"][0]) >= {"id", "observed_at", "property_changes", "significance"}
    # Findings delta compares the first and last snapshot cached findings.
    f = sec["findings"]
    assert f["baseline_snapshot_id"] != f["end_snapshot_id"]
    assert len(f["appeared"]) == t["findings_appeared"] // 2
    assert {x["id"] for x in f["appeared"]}.isdisjoint({x["id"] for x in f["cleared"]})


def test_connection_with_no_data_in_window_is_listed(client):
    a = client.post("/api/connections", json=_conn("Alpha")).json()["id"]
    b = client.post("/api/connections", json=_conn("Beta")).json()["id"]
    never = client.post("/api/connections", json=_conn("Gamma")).json()["id"]
    _scan(client, a, 2)
    _scan(client, b, 2)
    # Beta's scans happened three days ago; a 24 h window has nothing from it.
    _shift(b, timedelta(days=-3))
    since = quote((store.now() - timedelta(hours=24)).isoformat())
    body = client.get(f"/api/environment/changes?since={since}").json()
    assert body["window"] == "custom"
    t = body["totals"]
    assert t["connections"] == 3 and t["covered"] == 1 and t["no_data"] == 2
    beta = _section(body, b)
    assert beta["has_data"] is False and beta["changes"] == [] and beta["findings"] is None
    assert beta["snapshots_in_window"] == 0 and beta["counts"]["total"] == 0
    gamma = _section(body, never)
    assert gamma["has_data"] is False and gamma["name"] == "Gamma"
    assert t["changes"]["total"] == 15
    # "Last scan cycle" starts at the oldest latest-snapshot, which is Beta's,
    # so a default window covers everything scanned since then.
    body = client.get("/api/environment/changes").json()
    assert body["totals"]["covered"] == 2 and body["totals"]["changes"]["total"] == 30


def test_window_edges_are_inclusive(client):
    a = client.post("/api/connections", json=_conn("Alpha")).json()["id"]
    _scan(client, a, 2)
    observed = store.list_change_log(a, limit=1)[0].observed_at
    exact = quote(observed.isoformat())
    body = client.get(f"/api/environment/changes?since={exact}&until={exact}").json()
    assert body["totals"]["changes"]["total"] == 15
    assert _section(body, a)["snapshots_in_window"] == 1
    before = quote((observed - timedelta(seconds=1)).isoformat())
    body = client.get(f"/api/environment/changes?since={before}&until={before}").json()
    assert body["totals"]["changes"]["total"] == 0
    assert _section(body, a)["has_data"] is False
    # A window that ends before the observation excludes it; one that starts after does too.
    after = quote((observed + timedelta(seconds=1)).isoformat())
    later = quote((observed + timedelta(hours=1)).isoformat())
    assert (
        client.get(f"/api/environment/changes?since={after}&until={later}").json()["totals"][
            "changes"
        ]["total"]
        == 0
    )
    assert (
        client.get(f"/api/environment/changes?until={before}").json()["totals"]["changes"]["total"]
        == 0
    )
    # Offsets other than UTC are honoured, not compared as text.
    plus2 = quote((observed.astimezone(timedelta_tz(2))).isoformat())
    body = client.get(f"/api/environment/changes?since={plus2}&until={plus2}").json()
    assert body["totals"]["changes"]["total"] == 15


def timedelta_tz(hours: int):
    from datetime import timezone

    return timezone(timedelta(hours=hours))


def test_significance_filter_and_setting_default(client):
    a = client.post("/api/connections", json=_conn("Alpha")).json()["id"]
    _scan(client, a, 2)
    base = "/api/environment/changes"
    high = client.get(base + "?min_significance=high").json()
    assert high["min_significance"] == "high"
    assert high["totals"]["changes"] == {"high": 4, "medium": 0, "low": 0, "total": 4}
    sec = _section(high, a)
    assert len(sec["changes"]) == 4 and all(c["significance"] == "high" for c in sec["changes"])
    assert sec["has_data"] is True
    medium = client.get(base + "?min_significance=medium").json()
    assert medium["totals"]["changes"]["total"] == 13
    # The Settings floor applies when the parameter is omitted.
    client.put("/api/settings", json={"changes_min_significance": "high"})
    assert client.get(base).json()["totals"]["changes"]["total"] == 4
    assert client.get(base + "?min_significance=low").json()["totals"]["changes"]["total"] == 15
    # A floor that hides every row still counts the connection as covered.
    with db.transaction() as c:
        c.execute("UPDATE changes SET significance = 'low'")
    body = client.get(base + "?min_significance=high").json()
    assert body["totals"]["changes"]["total"] == 0
    assert _section(body, a)["has_data"] is True


def test_per_connection_limit_keeps_full_counts(client):
    a = client.post("/api/connections", json=_conn("Alpha")).json()["id"]
    _scan(client, a, 2)
    body = client.get("/api/environment/changes?limit_per_connection=3").json()
    sec = _section(body, a)
    assert len(sec["changes"]) == 3 and sec["truncated"] is True
    assert sec["counts"]["total"] == 15 and body["totals"]["changes"]["total"] == 15
    assert [c["significance"] for c in sec["changes"]] == ["high"] * 3


def test_validation(client):
    base = "/api/environment/changes"
    assert client.get(base + "?min_significance=bogus").status_code == 400
    assert client.get(base + "?since=yesterday").status_code == 400
    assert (
        client.get(base + "?since=2030-01-01T00:00:00Z&until=2020-01-01T00:00:00Z").status_code
        == 400
    )
    assert client.get(base + "?limit_per_connection=0").status_code == 422
    assert client.get(base + "?limit_per_connection=6000").status_code == 422
    assert client.get(base + "?since=2020-01-01T00:00:00Z").status_code == 200


def test_paused_connection_does_not_set_last_cycle_start(client):
    a = client.post("/api/connections", json=_conn("Alpha")).json()["id"]
    b = client.post("/api/connections", json=_conn("Beta")).json()["id"]
    _scan(client, a, 2)
    _scan(client, b, 2)
    _shift(b, timedelta(days=-30))
    client.put(f"/api/connections/{b}/schedule", json={"enabled": False})
    body = client.get("/api/environment/changes").json()
    # The window starts at Alpha's latest scan, so Beta (paused, a month old) has no data.
    assert store._dt(body["since"]) > store.now() - timedelta(hours=1)
    assert _section(body, b)["has_data"] is False
    assert _section(body, a)["has_data"] is True
    # With every schedule paused the paused ones decide the start instead.
    client.put(f"/api/connections/{a}/schedule", json={"enabled": False})
    body = client.get("/api/environment/changes").json()
    assert body["totals"]["covered"] == 2


def test_findings_delta_needs_cached_findings_on_both_ends(client):
    a = client.post("/api/connections", json=_conn("Alpha")).json()["id"]
    _scan(client, a, 2)
    sec = _section(client.get("/api/environment/changes").json(), a)
    assert sec["findings"] is not None and len(sec["findings"]["appeared"]) == 5
    baseline = sec["findings"]["baseline_snapshot_id"]
    with db.transaction() as c:
        c.execute("DELETE FROM findings WHERE snapshot_id = ?", (baseline,))
    body = client.get("/api/environment/changes").json()
    assert _section(body, a)["findings"] is None
    assert body["totals"]["findings_appeared"] == 0
    assert body["totals"]["findings_compared"] == 0


def test_findings_baseline_is_newest_snapshot_before_window(client):
    a = client.post("/api/connections", json=_conn("Alpha")).json()["id"]
    _scan(client, a, 2)
    snaps = client.get(f"/api/snapshots?connection_id={a}").json()
    newest, older = snaps[0], snaps[1]
    # A window that starts after the older snapshot: it still serves as the baseline.
    since = quote((store._dt(older["created_at"]) + timedelta(microseconds=1)).isoformat())
    sec = _section(client.get(f"/api/environment/changes?since={since}").json(), a)
    assert sec["snapshots_in_window"] == 1
    assert sec["findings"]["baseline_snapshot_id"] == older["id"]
    assert sec["findings"]["end_snapshot_id"] == newest["id"]
    # A window covering only the first scan has nothing to compare against.
    until = quote(older["created_at"])
    sec = _section(
        client.get(f"/api/environment/changes?since=2020-01-01T00:00:00Z&until={until}").json(), a
    )
    assert sec["has_data"] is True and sec["findings"] is None and sec["counts"]["total"] == 0


def test_active_connection_awaiting_first_scan_does_not_defer_to_paused(client):
    a = client.post("/api/connections", json=_conn("Alpha")).json()["id"]
    b = client.post("/api/connections", json=_conn("Beta")).json()["id"]
    _scan(client, b, 2)
    _shift(b, timedelta(days=-30))
    client.put(f"/api/connections/{b}/schedule", json={"enabled": False})
    # Alpha is scheduled but has never been scanned: the window falls back to
    # 24 h rather than stretching back to Beta's month-old snapshot.
    body = client.get("/api/environment/changes").json()
    assert store._dt(body["since"]) > store.now() - timedelta(hours=25)
    assert _section(body, a)["has_data"] is False
    assert _section(body, b)["has_data"] is False


def test_pruned_snapshots_are_reported_per_section(client):
    a = client.post("/api/connections", json=_conn("Alpha")).json()["id"]
    _scan(client, a, 2)
    sec = _section(client.get("/api/environment/changes").json(), a)
    assert sec["pruned_snapshot_ids"] == []
    older = sec["changes"][0]["from_snapshot_id"]
    deleted = client.delete(f"/api/snapshots/{older}")
    assert deleted.status_code == 200
    body = client.get("/api/environment/changes").json()
    sec = _section(body, a)
    # The change rows survive the prune and the section names the missing snapshot.
    assert len(sec["changes"]) == 15 and sec["pruned_snapshot_ids"] == [older]
    assert sec["findings"] is None and body["totals"]["findings_compared"] == 0
