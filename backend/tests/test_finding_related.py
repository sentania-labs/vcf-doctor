"""GET /api/findings/{id}/related walks back past identical snapshots (issue #5)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db, scheduler
from app.api import findings_related as fr
from app.api.router import _recent_changes
from app.main import app
from app.models import Resource
from app.models.change import Change
from app.models.finding import Finding
from app.models.resource import Relationship
from app.models.snapshot import RetentionPolicy
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
    db.reset_for_tests()
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
    deleted = client.delete(f"/api/snapshots/{snaps[2]['id']}")
    assert deleted.status_code == 200
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
    deleted = client.delete(f"/api/snapshots/{snaps[1]['id']}")
    assert deleted.status_code == 200
    finding = _finding(client, cid, "HOST_DISCONNECTED")
    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()
    assert body["window"]["scans_present"] == 1
    assert body["window"]["since"] == snaps[2]["created_at"]
    assert body["changes"][0]["summary"] == "connectionState connected -> disconnected"


def test_change_retention_recovers_the_cause_from_surviving_manual_snapshots(
    client, monkeypatch
):
    now = datetime(2026, 9, 9, 12, tzinfo=UTC)
    before_at = now - timedelta(days=31)
    cid = _connection(client, 0)
    host = Resource(
        id="host:retained",
        type="host",
        name="retained-host",
        source="test",
        properties={"connectionState": "connected"},
    )
    monkeypatch.setattr(store, "now", lambda: before_at)
    before = store.save_snapshot(cid, [host], "before", scheduled=False)
    store.save_findings(before.id, [])
    disconnected = host.model_copy(deep=True)
    disconnected.properties["connectionState"] = "disconnected"
    finding = Finding(
        id="test:retained-cause",
        check_id="HOST_DISCONNECTED",
        severity="critical",
        title="Host disconnected",
        summary="The host is disconnected",
        resource_id=disconnected.id,
        resource_type=disconnected.type,
        resource_name=disconnected.name,
    )
    monkeypatch.setattr(store, "now", lambda: before_at + timedelta(minutes=15))
    after = store.save_snapshot(cid, [disconnected], "after", scheduled=False)
    store.save_findings(after.id, [finding])
    changes = scheduler.compute_changes(before.resources, after.resources)
    store.save_changes(cid, before.id, after.id, after.created_at, changes)

    policy = RetentionPolicy(recent_days=1, hourly_days=7, daily_days=30)
    store.set_retention_policy(policy)
    monkeypatch.setattr(store, "now", lambda: now)
    assert store.apply_retention(cid, policy, at=now) == 0
    assert store.count_changes(cid) == 0
    retained_since = now - timedelta(days=30)
    assert store.log_since(cid) == before_at
    assert store.effective_log_since(cid) == retained_since

    response = client.get(f"/api/findings/{finding.id}/related?connection_id={cid}")
    assert response.status_code == 200
    body = response.json()
    assert body["window"]["basis"] == "pre_log_bracketing_pair"
    assert datetime.fromisoformat(body["window"]["log_starts_at"]) == retained_since
    assert [change["summary"] for change in body["changes"]] == [
        "connectionState connected -> disconnected"
    ]


def test_retained_cutoff_does_not_duplicate_the_bracketing_log_interval(client, monkeypatch):
    cutoff = datetime(2026, 8, 10, 10, tzinfo=UTC)
    now = cutoff + timedelta(days=30)
    cid = _connection(client, 0)
    host = Resource(
        id="host:cutoff",
        type="host",
        name="cutoff-host",
        source="test",
        properties={"connectionState": "connected"},
    )
    finding = Finding(
        id="test:cutoff",
        check_id="VM_SNAPSHOT_STALE",
        severity="warning",
        title="Persistent finding",
        summary="The finding remains present",
        resource_id=host.id,
        resource_type=host.type,
        resource_name=host.name,
    )
    monkeypatch.setattr(store, "now", lambda: cutoff - timedelta(minutes=10))
    previous = store.save_snapshot(cid, [host], "before", scheduled=False)
    store.save_findings(previous.id, [])
    states = [
        (cutoff + timedelta(minutes=5), "disconnected"),
        (cutoff + timedelta(minutes=20), "connected"),
        (cutoff + timedelta(minutes=35), "disconnected"),
    ]
    for observed_at, state in states:
        current_host = host.model_copy(deep=True)
        current_host.properties["connectionState"] = state
        monkeypatch.setattr(store, "now", lambda at=observed_at: at)
        current = store.save_snapshot(cid, [current_host], state, scheduled=False)
        store.save_findings(current.id, [finding])
        changes = scheduler.compute_changes(previous.resources, current.resources)
        store.save_changes(cid, previous.id, current.id, current.created_at, changes)
        previous = current
        host = current_host

    policy = RetentionPolicy(recent_days=1, hourly_days=7, daily_days=30)
    store.set_retention_policy(policy)
    monkeypatch.setattr(store, "now", lambda: now)
    store.apply_retention(cid, policy, at=now)
    assert store.count_changes(cid) == 3

    response = client.get(f"/api/findings/{finding.id}/related?connection_id={cid}")
    assert response.status_code == 200
    body = response.json()
    assert body["window"]["basis"] == "pre_log_bracketing_pair"
    assert datetime.fromisoformat(body["window"]["log_starts_at"]) == cutoff
    assert [change["summary"] for change in body["changes"]] == [
        "connectionState connected -> disconnected",
        "connectionState disconnected -> connected",
        "connectionState connected -> disconnected",
    ]


def test_rows_ending_at_the_pre_finding_snapshot_are_excluded(client):
    """A row stamped with the snapshot before the finding appeared belongs to the
    diff that ended there (Z -> A), not to the A -> B interval that introduced it."""
    cid = _connection(client, 3)
    snaps = client.get(f"/api/snapshots?connection_id={cid}").json()
    finding = _finding(client, cid, "HOST_DISCONNECTED")
    stale = Change(
        change_type="modified",
        resource_id=finding["resource_id"],
        resource_type="host",
        resource_name="esx03",
        significance="high",
        summary="STALE: before the finding",
    )
    store.save_changes(cid, "z", snaps[2]["id"], store.list_snapshots(cid)[2].created_at, [stale])
    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()
    summaries = [c["summary"] for c in body["changes"]]
    assert "STALE: before the finding" not in summaries
    assert summaries[0] == "connectionState connected -> disconnected"


def _forget_the_change_log(cid: str) -> None:
    """Make the database look like one from before the change log: no rows and
    no record of the log ever having started."""
    with db.transaction() as c:
        c.execute("DELETE FROM changes WHERE connection_id = %s", (cid,))
        c.execute("DELETE FROM settings WHERE key = %s", (f"{store.LOG_SINCE_KEY}:{cid}",))


def test_no_log_falls_back_to_latest_differing_pair(client):
    """A database from before the change log has no rows: diff the newest pair that differs."""
    cid = _connection(client, 3)
    _forget_the_change_log(cid)
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


def _scan_with_another_host_powered_off(cid: str) -> None:
    """A fourth snapshot that differs from the newest by a high-significance
    change on an object unrelated to the disconnected host, so the newest pair
    is no longer identical and the change log has a real row to start from."""
    latest = store.latest_snapshot(cid)
    resources = [r.model_copy(deep=True) for r in latest.resources]
    other = next(
        r
        for r in resources
        if r.type == "host" and r.properties.get("connectionState") == "connected"
    )
    other.properties["powerState"] = "poweredOff"
    snap = store.save_snapshot(cid, resources, "power event", scheduled=True)
    store.save_findings(snap.id, store.get_findings(latest.id))
    diff = scheduler.compute_changes(latest.resources, resources)
    assert any(c.significance == "high" and c.resource_id == other.id for c in diff)
    store.save_changes(cid, latest.id, snap.id, snap.created_at, diff)


def _restart_log_at_the_newest_interval(client, cid: str) -> dict:
    """Make the database look like one upgraded to the change log mid-life:
    the log only starts at the newest scan interval, long after the finding
    did. The newest pair differs, as it does on a live estate, so the log's
    own interval is not where the cause hides."""
    _forget_the_change_log(cid)
    _scan_with_another_host_powered_off(cid)
    snaps = store.list_snapshots(cid)  # newest first
    assert store.log_since(cid) == snaps[1].created_at
    unrelated = Change(
        change_type="modified",
        resource_id=f"datastore:{cid}:datastore-99",
        resource_type="datastore",
        resource_name="ds-logs",
        significance="medium",
        summary="usage 40.0% -> 41.0%",
    )
    store.save_changes(cid, snaps[1].id, snaps[0].id, snaps[0].created_at, [unrelated])
    return {"snaps": snaps}


def test_finding_older_than_the_change_log_gets_a_window_that_can_hold_the_cause(client):
    """Issue #41: a log that starts after the finding did cannot contain the
    cause, so the drawer diffs the two snapshots around first observation and
    says where the log begins instead of showing an empty change-log window.
    Scans go A, B, B, C: the finding appeared in the A to B interval, and the
    newest pair (B to C) differs, so the newest differing pair is the wrong
    answer here."""
    cid = _connection(client, 3)
    _restart_log_at_the_newest_interval(client, cid)
    stamps = [s["created_at"] for s in client.get(f"/api/snapshots?connection_id={cid}").json()]
    finding = _finding(client, cid, "HOST_DISCONNECTED")

    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()

    assert body["window"]["basis"] == "pre_log_bracketing_pair"
    assert body["window"]["log_starts_at"] == stamps[1]
    assert body["window"]["first_observed"] == stamps[2]
    assert body["window"]["since"] == stamps[3]
    assert body["window"]["until"] == stamps[2]
    assert body["changes"][0]["resource_id"] == finding["resource_id"]
    assert body["changes"][0]["summary"] == "connectionState connected -> disconnected"
    summaries = [c["summary"] for c in body["changes"]]
    # The logged rows follow the bracketing diff; the medium one is not near and is dropped.
    assert "usage 40.0% -> 41.0%" not in summaries
    logged_high = next(i for i, s in enumerate(summaries) if "poweredOff" in s)
    assert logged_high > 0


def test_pre_log_fallback_uses_the_newest_differing_pair_once_the_bracketing_pair_is_pruned(
    client,
):
    """When retention has removed the snapshot before first observation, the
    bracketing pair cannot be diffed; the newest differing pair is shown and
    the window says so rather than claiming to bracket the finding."""
    cid = _connection(client, 3)
    _restart_log_at_the_newest_interval(client, cid)
    snaps = store.list_snapshots(cid)
    assert store.delete_snapshots([snaps[3].id]) == 1
    stamps = [s["created_at"] for s in client.get(f"/api/snapshots?connection_id={cid}").json()]
    finding = _finding(client, cid, "HOST_DISCONNECTED")

    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()

    assert body["window"]["basis"] == "pre_log_differing_pair"
    assert body["window"]["log_starts_at"] == stamps[1]
    assert body["window"]["since"] == stamps[1]
    assert body["window"]["until"] == stamps[0]
    assert body["changes"] and all(c["significance"] == "high" for c in body["changes"])
    assert any("poweredOff" in c["summary"] for c in body["changes"])


def test_a_complete_change_log_is_never_reported_as_starting_late(client):
    """A database born with the change log covers its whole history: the
    fallback must not fire just because the oldest row is younger than the
    finding's first snapshot."""
    cid = _connection(client, 3)
    for check in ("HOST_DISCONNECTED", "VM_SNAPSHOT_STALE"):
        finding = _finding(client, cid, check)
        body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()
        assert body["window"]["basis"] == "first_observed", check
        assert body["window"]["log_starts_at"] is None, check


def test_pre_log_bracketing_diff_leads_even_when_the_log_has_later_rows_on_the_object(client):
    """A host that keeps changing after it disconnected logs rows about itself
    once the log starts; those are not the cause. The bracketing diff still
    comes first and the logged rows follow it."""
    cid = _connection(client, 3)
    snaps = _restart_log_at_the_newest_interval(client, cid)["snaps"]
    stamps = [s["created_at"] for s in client.get(f"/api/snapshots?connection_id={cid}").json()]
    finding = _finding(client, cid, "HOST_DISCONNECTED")
    related = Change(
        change_type="modified",
        resource_id=finding["resource_id"],
        resource_type="host",
        resource_name="esx03",
        significance="high",
        summary="LOGGED: still disconnected",
    )
    store.save_changes(cid, snaps[1].id, snaps[0].id, snaps[0].created_at, [related])

    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()

    assert body["window"]["basis"] == "pre_log_bracketing_pair"
    assert body["window"]["log_starts_at"] == stamps[1]
    assert body["window"]["since"] == stamps[3]
    assert body["window"]["until"] == stamps[2]
    own = [c["summary"] for c in body["changes"] if c["resource_id"] == finding["resource_id"]]
    assert own == ["connectionState connected -> disconnected", "LOGGED: still disconnected"]
    assert body["changes"][0]["summary"] == "connectionState connected -> disconnected"


def test_a_quiet_estate_is_not_mistaken_for_a_late_change_log(client):
    """Scans S1 to S5 see identical resources, so the log records nothing until
    S5 to S6; a time-based finding first appearing at S3 is still inside the
    log's coverage and must not be labelled older than the log. Rows alone
    cannot tell this apart from a mid-life upgrade; the log_since marker can."""
    cid = _connection(client, 1)
    s1 = store.latest_snapshot(cid)
    findings = store.get_findings(s1.id)
    finding = next(f for f in findings if f.check_id == "VM_SNAPSHOT_STALE")
    without = [f for f in findings if f.id != finding.id]
    store.save_findings(s1.id, without)
    previous = s1
    for n in range(2, 6):
        snap = store.save_snapshot(cid, s1.resources, f"S{n}", scheduled=True)
        store.save_findings(snap.id, without if n == 2 else findings)
        diff = scheduler.compute_changes(previous.resources, snap.resources)
        assert diff == []
        store.save_changes(cid, previous.id, snap.id, snap.created_at, diff)
        previous = snap
    assert store.log_since(cid) == s1.created_at
    assert store.count_changes(cid) == 0
    _scan_with_another_host_powered_off(cid)
    assert store.count_changes(cid) > 0

    body = client.get(f"/api/findings/{finding.id}/related?connection_id={cid}").json()

    assert body["window"]["basis"] == "first_observed"
    assert body["window"]["log_starts_at"] is None
    assert body["window"]["scans_present"] == 4


def test_startup_backfills_log_since_from_the_oldest_surviving_row(client):
    """A database that already has change rows but predates the marker gets
    one at startup: the snapshot its oldest row diffed from. The marker is
    internal and never surfaces as a setting."""
    cid = _connection(client, 3)
    snaps = store.list_snapshots(cid)
    with db.transaction() as c:
        c.execute("DELETE FROM settings WHERE key = %s", (f"{store.LOG_SINCE_KEY}:{cid}",))
    assert store.log_since(cid) is None

    assert store.backfill_log_since() == {cid: snaps[2].created_at}
    assert store.log_since(cid) == snaps[2].created_at
    assert store.backfill_log_since() == {cid: snaps[2].created_at}

    assert "log_since" not in client.get("/api/settings").json()
    finding = _finding(client, cid, "HOST_DISCONNECTED")
    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()
    assert body["window"]["basis"] == "first_observed"
    assert body["window"]["log_starts_at"] is None


def test_pruned_backfill_source_does_not_replay_the_first_logged_change(client, monkeypatch):
    now = datetime(2026, 9, 9, 12, tzinfo=UTC)
    cid = _connection(client, 0)
    connected_a = Resource(
        id="host:backfill-a",
        type="host",
        name="backfill-host-a",
        source="test",
        properties={"connectionState": "connected"},
    )
    connected_b = Resource(
        id="host:backfill-b",
        type="host",
        name="backfill-host-b",
        source="test",
        properties={"connectionState": "connected"},
    )
    disconnected_a = connected_a.model_copy(deep=True)
    disconnected_a.properties["connectionState"] = "disconnected"
    disconnected_b = connected_b.model_copy(deep=True)
    disconnected_b.properties["connectionState"] = "disconnected"
    finding = Finding(
        id="test:backfill",
        check_id="HOST_DISCONNECTED",
        severity="critical",
        title="Host disconnected",
        summary="The host is disconnected",
        resource_id=disconnected_a.id,
        resource_type=disconnected_a.type,
        resource_name=disconnected_a.name,
    )

    monkeypatch.setattr(store, "now", lambda: now - timedelta(minutes=15))
    s0 = store.save_snapshot(cid, [connected_a, connected_b], "S0", scheduled=False)
    store.save_findings(s0.id, [])
    monkeypatch.setattr(store, "now", lambda: now - timedelta(minutes=10))
    s1 = store.save_snapshot(cid, [connected_a, disconnected_b], "S1", scheduled=True)
    store.save_findings(s1.id, [])
    monkeypatch.setattr(store, "now", lambda: now - timedelta(minutes=5))
    s2 = store.save_snapshot(cid, [disconnected_a, connected_b], "S2", scheduled=True)
    store.save_findings(s2.id, [finding])
    store.save_changes(
        cid,
        s1.id,
        s2.id,
        s2.created_at,
        scheduler.compute_changes(s1.resources, s2.resources),
    )
    assert store.delete_snapshots([s1.id]) == 1
    with db.transaction() as c:
        c.execute("DELETE FROM settings WHERE key = %s", (f"{store.LOG_SINCE_KEY}:{cid}",))
    assert store.backfill_log_since() == {cid: s2.created_at}
    monkeypatch.setattr(store, "now", lambda: now)

    overview = client.get(f"/api/overview?connection_id={cid}&min_significance=high")
    assert overview.status_code == 200
    overview_changes = overview.json()["recent_changes"]
    assert {
        (change["resource_id"], change["summary"]) for change in overview_changes
    } == {
        (disconnected_a.id, "connectionState connected -> disconnected"),
        (connected_b.id, "connectionState disconnected -> connected"),
    }

    response = client.get(f"/api/findings/{finding.id}/related?connection_id={cid}")
    assert response.status_code == 200
    body = response.json()
    assert body["window"]["basis"] == "pre_log_bracketing_pair"
    assert [(change["resource_id"], change["summary"]) for change in body["changes"]] == [
        (disconnected_a.id, "connectionState connected -> disconnected"),
        (connected_b.id, "connectionState disconnected -> connected"),
    ]


def test_overview_recovers_the_full_24_hour_window(client, monkeypatch):
    now = datetime(2026, 9, 9, 12, 30, tzinfo=UTC)
    cid = _connection(client, 0)
    connected = Resource(
        id="host:early",
        type="host",
        name="early-host",
        source="test",
        properties={"connectionState": "connected"},
    )
    previous = None
    for index in range(14):
        observed_at = now - timedelta(hours=3, minutes=30) + timedelta(minutes=15 * index)
        host = connected.model_copy(deep=True)
        if index >= 3:
            host.properties["connectionState"] = "disconnected"
        monkeypatch.setattr(store, "now", lambda at=observed_at: at)
        current = store.save_snapshot(cid, [host], f"S{index}", scheduled=True)
        store.save_findings(current.id, [])
        if index == 13:
            assert previous is not None
            store.save_changes(cid, previous.id, current.id, current.created_at, [])
        previous = current
    monkeypatch.setattr(store, "now", lambda: now)

    response = client.get(f"/api/overview?connection_id={cid}&min_significance=high")

    assert response.status_code == 200
    assert [change["summary"] for change in response.json()["recent_changes"]] == [
        "connectionState connected -> disconnected"
    ]


def _copy_snapshots(cid: str, count: int) -> None:
    """More snapshots holding the same findings, like an estate on 15-minute scans."""
    latest = store.latest_snapshot(cid)
    findings = store.get_findings(latest.id)
    for i in range(count):
        snap = store.save_snapshot(cid, latest.resources, f"copy {i}", scheduled=True)
        store.save_findings(snap.id, findings)


def test_locating_first_observation_does_not_decode_every_snapshot(client, monkeypatch):
    """Issue #40: one SQL query locates the snapshots holding the finding, so
    opening the drawer no longer decodes a findings blob per snapshot walked."""
    cid = _connection(client, 3)
    _copy_snapshots(cid, 15)
    finding = _finding(client, cid, "HOST_DISCONNECTED")
    decodes: list[str] = []
    real = store.get_findings
    monkeypatch.setattr(store, "get_findings", lambda sid: (decodes.append(sid), real(sid))[1])

    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={cid}").json()

    # 17 snapshots hold the finding, and only the newest one is decoded (to read
    # the finding itself). Before the prefilter this was one decode per snapshot.
    assert body["window"]["scans_present"] == 17
    assert len(decodes) == 1, decodes


def test_prefilter_matches_the_finding_id_exactly(client):
    """Finding ids are full of underscores, which LIKE treats as wildcards, and
    one id must never match a longer one."""
    cid = _connection(client, 1)
    finding = _finding(client, cid, "VM_SNAPSHOT_STALE")
    snaps = store.list_snapshots(cid)
    assert store.snapshot_ids_with_finding(cid, finding["id"]) == {snaps[0].id}
    assert store.snapshot_ids_with_finding(cid, finding["id"] + "-EXTRA") == set()
    wildcarded = finding["id"].replace("_", "%")
    assert store.snapshot_ids_with_finding(cid, wildcarded) == set()


def test_connection_coverage_is_independent_of_scan_order(client, monkeypatch):
    start = datetime(2026, 9, 9, 10, tzinfo=UTC)
    monkeypatch.setattr(store, "now", lambda: start)
    a = _connection(client, 1)
    monkeypatch.setattr(store, "now", lambda: start + timedelta(minutes=1))
    b = _connection(client, 1)
    monkeypatch.setattr(store, "now", lambda: start + timedelta(minutes=10))
    _scan(client, b, 1)
    monkeypatch.setattr(store, "now", lambda: start + timedelta(minutes=15))
    _scan(client, a, 1)

    assert store.log_since(a) == start
    assert store.log_since(b) == start + timedelta(minutes=1)
    finding = _finding(client, a, "HOST_DISCONNECTED")
    body = client.get(f"/api/findings/{finding['id']}/related?connection_id={a}").json()
    assert body["window"]["basis"] == "first_observed"
    assert body["window"]["log_starts_at"] is None
    feed = _recent_changes(a, "medium")
    logged = store.list_change_log(a, min_significance="medium")
    assert sorted(c.summary for c in feed) == sorted(c.summary for c in logged)

    with db.transaction() as c:
        c.execute("DELETE FROM settings WHERE key IN (%s, %s)", (
            f"{store.LOG_SINCE_KEY}:{a}", f"{store.LOG_SINCE_KEY}:{b}",
        ))
    db.set_setting(store.LOG_SINCE_KEY, (start + timedelta(minutes=1)).isoformat())
    expected = {a: start, b: start + timedelta(minutes=1)}
    assert store.backfill_log_since() == expected
    assert store.backfill_log_since() == expected
    assert store.log_since(a) == start
    assert store.log_since(b) == expected[b]
    assert not any(k.startswith("log_since") for k in client.get("/api/settings").json())


def test_bracketing_parent_change_survives_a_full_page_of_logged_object_changes(client):
    cid = _connection(client, 0)
    host = Resource(
        id="host:parent", type="host", name="parent", source="test",
        properties={"connectionState": "connected"},
    )
    vm = Resource(id="vm:child", type="vm", name="child", source="test", parent_id=host.id)
    finding = Finding(
        id="test:parent", check_id="TEST", severity="warning",
        title="Parent failed", summary="Host disconnected",
        resource_id=vm.id, resource_type="vm", resource_name=vm.name,
    )
    before = store.save_snapshot(cid, [host, vm], "before", scheduled=True)
    store.save_findings(before.id, [])
    host.properties["connectionState"] = "disconnected"
    after = store.save_snapshot(cid, [host, vm], "after", scheduled=True)
    store.save_findings(after.id, [finding])
    latest = store.save_snapshot(cid, [host, vm], "latest", scheduled=True)
    store.save_findings(latest.id, [finding])
    rows = [
        Change(
            change_type="modified", resource_id=vm.id, resource_type="vm",
            resource_name=vm.name, significance="high", summary=f"Later flap {i}",
        )
        for i in range(fr.MAX_CHANGES + 2)
    ]
    store.save_changes(cid, after.id, latest.id, latest.created_at, rows)

    body = client.get(f"/api/findings/{finding.id}/related?connection_id={cid}").json()
    assert body["window"]["basis"] == "pre_log_bracketing_pair"
    assert len(body["changes"]) == fr.MAX_CHANGES
    assert body["changes"][0]["resource_id"] == host.id
    assert body["changes"][0]["summary"] == "connectionState connected -> disconnected"
    assert all(c["resource_id"] == vm.id for c in body["changes"][1:])


@pytest.mark.parametrize("pruned", [False, True], ids=["bracketing", "fallback"])
def test_snapshot_diff_keeps_distinct_logged_disconnects(client, pruned):
    cid = _connection(client, 0)
    host = Resource(
        id="host:parent", type="host", name="parent", source="test",
        properties={"connectionState": "connected"},
    )
    vm = Resource(id="vm:child", type="vm", name="child", source="test", parent_id=host.id)
    finding = Finding(
        id="test:stale", check_id="VM_SNAPSHOT_STALE", severity="warning",
        title="Stale snapshot", summary="Snapshot remains stale", resource_id=vm.id,
        resource_type="vm", resource_name=vm.name,
    )
    previous = store.save_snapshot(cid, [host, vm], "before", scheduled=True)
    store.save_findings(previous.id, [])
    before_id = previous.id
    states = ("disconnected", "connected", "disconnected")
    if pruned:
        states = ("connected", "connected", *states)
    for i, state in enumerate(states):
        host = host.model_copy(deep=True)
        host.properties["connectionState"] = state
        snap = store.save_snapshot(cid, [host, vm], state, scheduled=True)
        store.save_findings(snap.id, [finding])
        if i >= (2 if pruned else 1):
            store.save_changes(
                cid, previous.id, snap.id, snap.created_at,
                scheduler.compute_changes(previous.resources, snap.resources),
            )
        previous = snap

    if pruned:
        assert store.delete_snapshots([before_id]) == 1

    response = client.get(f"/api/findings/{finding.id}/related?connection_id={cid}")
    assert response.status_code == 200
    body = response.json()
    expected_basis = "pre_log_differing_pair" if pruned else "pre_log_bracketing_pair"
    assert body["window"]["basis"] == expected_basis
    expected = [
        "connectionState connected -> disconnected",
        "connectionState disconnected -> connected",
        "connectionState connected -> disconnected",
    ]
    if pruned:
        expected = [
            "connectionState connected -> disconnected",
            "connectionState connected -> disconnected",
            "connectionState disconnected -> connected",
        ]
    assert [c["summary"] for c in body["changes"]] == expected


def test_newer_recovered_change_survives_overview_cap_across_connections(client, monkeypatch):
    start = datetime(2026, 9, 9, 7, tzinfo=UTC)
    a, b = _connection(client, 0), _connection(client, 0)

    def snapshot(cid, hour, count, state):
        monkeypatch.setattr(store, "now", lambda: start + timedelta(hours=hour))
        resources = [
            Resource(
                id=f"host:{cid}:{i}", type="host", name=f"host-{i}", source=cid,
                properties={"connectionState": state},
            )
            for i in range(count)
        ]
        snap = store.save_snapshot(cid, resources, state, scheduled=True)
        store.save_findings(snap.id, [])
        return snap

    snapshot(a, 0, 1, "connected")
    before_b = snapshot(b, 0, 5, "connected")
    after_b = snapshot(b, 1, 5, "disconnected")
    store.save_changes(
        b, before_b.id, after_b.id, after_b.created_at,
        scheduler.compute_changes(before_b.resources, after_b.resources),
    )
    recovered = snapshot(a, 2, 1, "disconnected")
    latest = snapshot(a, 3, 1, "disconnected")
    store.save_changes(a, recovered.id, latest.id, latest.created_at, [])

    response = client.get("/api/overview?min_significance=high")
    assert response.status_code == 200
    feed = response.json()["recent_changes"]
    assert len(feed) == 5
    assert feed[0]["resource_id"] == recovered.resources[0].id
    assert datetime.fromisoformat(feed[0]["observed_at"]) == recovered.created_at
    assert all(c["resource_id"].startswith(f"host:{b}:") for c in feed[1:])


def test_a_failed_row_write_does_not_leave_the_coverage_marker_set(client):
    """The marker must commit with the rows it describes. Setting it first and
    failing afterwards would leave the connection claiming an interval it never
    stored, and nothing would ever correct it: the marker is written once, so
    pre-log recovery would skip the interval holding the cause."""
    cid = _connection(client, 2)
    snaps = store.list_snapshots(cid)  # newest first
    with db.transaction() as c:
        c.execute("DELETE FROM changes WHERE connection_id = %s", (cid,))
        c.execute("DELETE FROM settings WHERE key = %s", (f"{store.LOG_SINCE_KEY}:{cid}",))
    assert store.log_since(cid) is None

    change = Change(
        change_type="modified",
        resource_id=f"host:{cid}:esx03",
        resource_type="host",
        resource_name="esx03",
        significance="high",
        summary="connectionState connected -> disconnected",
    )
    # A row write that fails after the marker would otherwise have been set.
    original = store.new_id
    store.new_id = lambda: (_ for _ in ()).throw(RuntimeError("row write failed"))
    try:
        with pytest.raises(RuntimeError):
            store.save_changes(cid, snaps[1].id, snaps[0].id, snaps[0].created_at, [change])
    finally:
        store.new_id = original

    assert store.count_changes(cid) == 0
    assert store.log_since(cid) is None  # not left claiming an interval it never stored

    # The next successful scan writes both together.
    store.save_changes(cid, snaps[1].id, snaps[0].id, snaps[0].created_at, [change])
    assert store.count_changes(cid) == 1
    assert store.log_since(cid) == snaps[1].created_at


def test_an_empty_diff_still_marks_coverage_atomically(client):
    """The intentional empty-diff case still stamps the marker, in its own
    transaction, so a quiet estate is not mistaken for a mid-life upgrade."""
    cid = _connection(client, 2)
    snaps = store.list_snapshots(cid)
    with db.transaction() as c:
        c.execute("DELETE FROM changes WHERE connection_id = %s", (cid,))
        c.execute("DELETE FROM settings WHERE key = %s", (f"{store.LOG_SINCE_KEY}:{cid}",))

    assert store.save_changes(cid, snaps[1].id, snaps[0].id, snaps[0].created_at, []) == 0

    assert store.count_changes(cid) == 0
    assert store.log_since(cid) == snaps[1].created_at
