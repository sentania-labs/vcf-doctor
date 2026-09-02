"""GET /api/findings/{id}/related walks back past identical snapshots (issue #5)."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api import findings_related as fr
from app.main import app
from app.models import Resource
from app.models.change import Change
from app.models.finding import Finding
from app.models.resource import Relationship
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


def _scan(client, cid: str, times: int) -> None:
    for _ in range(times):
        assert client.post("/api/scan", json={"connection_id": cid}).json()[0]["status"] == "ok"


def _connection(client, scans: int) -> str:
    cid = client.post("/api/connections", json=FIXTURE_CONN).json()["id"]
    _scan(client, cid, scans)
    return cid


def _finding(client, cid: str, check_id: str) -> dict:
    found = [
        f
        for f in client.get(f"/api/findings?connection_id={cid}").json()
        if f["check_id"] == check_id
    ]
    assert found, check_id
    return found[0]


def test_third_identical_scan_still_shows_the_cause(client):
    """Fixture scans go A, B, B: the newest pair is identical, the cause is one scan older."""
    cid = _connection(client, 3)
    assert client.get(f"/api/changes?connection_id={cid}").json() == []
    finding = _finding(client, cid, "HOST_DISCONNECTED")
    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()
    snaps = client.get(f"/api/snapshots?connection_id={cid}").json()  # newest first
    assert body["window"]["basis"] == "first_observed"
    assert body["window"]["scans_present"] == 2
    assert body["window"]["first_observed"] == snaps[1]["created_at"]
    # The window opens at the snapshot before: that scan interval is when the host dropped.
    assert body["window"]["since"] == snaps[2]["created_at"]
    assert body["window"]["until"] is None
    assert body["window"]["capped"] is False
    assert body["resource_ids"][0] == finding["resource_id"]
    own = [c for c in body["changes"] if c["resource_id"] == finding["resource_id"]]
    assert own and own[0]["summary"] == "connectionState connected -> disconnected"
    # The object's own rows come first, then the rest of the connection's high rows.
    assert body["changes"][0]["resource_id"] == finding["resource_id"]
    assert all(
        c["significance"] == "high"
        for c in body["changes"]
        if c["resource_id"] not in body["resource_ids"]
    )


def test_connection_is_resolved_from_the_finding_when_omitted(client):
    cid = _connection(client, 2)
    finding = _finding(client, cid, "HOST_DISCONNECTED")
    body = client.get(f"/api/findings/{finding['id']}/related").json()
    assert body["connection_id"] == cid
    assert body["window"]["scans_present"] == 1


def test_finding_present_since_the_first_snapshot(client):
    """Nothing introduced it (no diff before the first snapshot); the window starts there."""
    cid = _connection(client, 3)
    finding = _finding(client, cid, "VM_SNAPSHOT_STALE")
    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()
    snaps = client.get(f"/api/snapshots?connection_id={cid}").json()
    assert body["window"]["scans_present"] == 3
    assert body["window"]["since"] == snaps[-1]["created_at"]
    assert body["window"]["first_observed"] == snaps[-1]["created_at"]


def test_pruned_snapshots_do_not_hide_the_logged_cause(client):
    """Retention keeps the log longer than snapshots: with only the two identical
    snapshots left, the introducing diff is still found in the log."""
    cid = _connection(client, 3)
    snaps = client.get(f"/api/snapshots?connection_id={cid}").json()
    assert client.delete(f"/api/snapshots/{snaps[2]['id']}").status_code == 200
    finding = _finding(client, cid, "HOST_DISCONNECTED")
    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()
    assert body["window"]["scans_present"] == 2
    assert body["window"]["first_observed"] == snaps[1]["created_at"]
    assert body["window"]["since"] == snaps[1]["created_at"]
    assert body["changes"][0]["summary"] == "connectionState connected -> disconnected"


def test_pruned_middle_snapshot_keeps_the_cause_in_the_query(client):
    """A, B, B with the first B pruned: the introducing row is stamped with the
    pruned snapshot, between the surviving A and B. It must still be fetched."""
    cid = _connection(client, 3)
    snaps = client.get(f"/api/snapshots?connection_id={cid}").json()
    assert client.delete(f"/api/snapshots/{snaps[1]['id']}").status_code == 200
    finding = _finding(client, cid, "HOST_DISCONNECTED")
    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()
    assert body["window"]["scans_present"] == 1
    assert body["window"]["since"] == snaps[2]["created_at"]
    assert body["changes"][0]["summary"] == "connectionState connected -> disconnected"


def test_no_log_falls_back_to_latest_differing_pair(client):
    """A database from before the change log has no rows: diff the newest pair that differs."""
    cid = _connection(client, 3)
    with db.transaction() as c:
        c.execute("DELETE FROM changes WHERE connection_id = ?", (cid,))
    finding = _finding(client, cid, "HOST_DISCONNECTED")
    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()
    snaps = client.get(f"/api/snapshots?connection_id={cid}").json()
    assert body["window"]["basis"] == "latest_differing_pair"
    assert body["window"]["since"] == snaps[2]["created_at"]
    assert body["window"]["until"] == snaps[1]["created_at"]
    assert body["changes"][0]["resource_id"] == finding["resource_id"]


def test_no_log_and_nothing_ever_differed(client):
    cid = _connection(client, 1)
    finding = _finding(client, cid, "VM_SNAPSHOT_STALE")
    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()
    assert body["window"]["basis"] == "latest_differing_pair"
    assert body["window"]["since"] is None and body["window"]["until"] is None
    assert body["changes"] == []


def test_window_is_capped(client, monkeypatch):
    cid = _connection(client, 3)
    finding = _finding(client, cid, "HOST_DISCONNECTED")
    monkeypatch.setattr(fr, "MAX_WINDOW", timedelta(0))
    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()
    assert body["window"]["capped"] is True
    assert body["window"]["since"] > body["window"]["first_observed"]
    assert body["changes"] == []


def test_walk_stops_at_max_scans_back(client, monkeypatch):
    cid = _connection(client, 3)
    finding = _finding(client, cid, "VM_SNAPSHOT_STALE")
    monkeypatch.setattr(fr, "MAX_SCANS_BACK", 2)
    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()
    assert body["window"]["scans_present"] == 2
    assert body["window"]["capped"] is True


def test_unknown_finding_and_connection_are_404(client):
    cid = _connection(client, 1)
    assert client.get("/api/findings/nope/related").status_code == 404
    assert client.get(f"/api/findings/nope/related?connection_id={cid}").status_code == 404
    finding = _finding(client, cid, "VM_SNAPSHOT_STALE")
    assert (
        client.get(f"/api/findings/{finding['id']}/related?connection_id=ghost").status_code == 404
    )


def test_neighbourhood_is_object_parent_children_and_relations():
    cluster = Resource(id="cluster:c1", type="cluster", name="c1", source="vcenter:x")
    host = Resource(
        id="host:h1",
        type="host",
        name="h1",
        source="vcenter:x",
        parent_id="cluster:c1",
        relationships=[Relationship(kind="uses", target_id="datastore:d1")],
    )
    vm = Resource(id="vm:v1", type="vm", name="v1", source="vcenter:x", parent_id="host:h1")
    other = Resource(id="vm:v2", type="vm", name="v2", source="vcenter:x", parent_id="host:h9")
    finding = Finding(
        id="X:host:h1",
        check_id="X",
        severity="warning",
        title="t",
        summary="s",
        resource_id="host:h1",
    )
    assert fr.neighbourhood(finding, [cluster, host, vm, other]) == [
        "host:h1",
        "cluster:c1",
        "datastore:d1",
        "vm:v1",
    ]
    estate_wide = Finding(id="Y", check_id="Y", severity="info", title="t", summary="s")
    assert fr.neighbourhood(estate_wide, [cluster, host]) == []


def test_select_keeps_neighbourhood_then_high_only():
    def ch(rid, sig):
        return Change(
            change_type="modified",
            resource_id=rid,
            resource_type="vm",
            resource_name=rid,
            significance=sig,
        )

    rows = [ch("a", "low"), ch("b", "low"), ch("c", "high"), ch("d", "medium")]
    picked = fr._select(rows, ["a"])
    assert [c.resource_id for c in picked] == ["a", "c"]
    # An estate-wide finding (empty neighbourhood) only sees the high rows.
    assert [c.resource_id for c in fr._select(rows, [])] == ["c"]


def test_select_keeps_the_oldest_rows_when_capped():
    """A flapping object logs a row every scan; the introducing change (oldest) must survive."""
    from datetime import UTC, datetime, timedelta

    from app.models.change import ChangeRecord

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        ChangeRecord(
            id=f"r{i}",
            connection_id="c",
            from_snapshot_id="x",
            to_snapshot_id="y",
            observed_at=t0 + timedelta(minutes=i),
            change_type="modified",
            resource_id="vm:a",
            resource_type="vm",
            resource_name="a",
            significance="low",
            summary="CAUSE" if i == 0 else f"flap {i}",
        )
        for i in range(fr.MAX_CHANGES + 5)
    ]
    picked = fr._select(list(reversed(rows)), ["vm:a"])
    assert len(picked) == fr.MAX_CHANGES
    assert picked[0].summary == "CAUSE"


def test_first_observed_walks_back_with_store(client):
    cid = _connection(client, 3)
    finding = _finding(client, cid, "HOST_DISCONNECTED")
    first = fr.first_observed(cid, finding["id"])
    snaps = store.list_snapshots(cid)
    assert (first.seen_at, first.interval_start, first.count, first.capped) == (
        snaps[1].created_at,
        snaps[2].created_at,
        2,
        False,
    )
    assert fr.first_observed(cid, "nope") == fr.FirstObserved()
