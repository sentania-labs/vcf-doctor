"""Health score: severity-weighted, per-object normalised, floored at 0,
"passed" separated from "not evaluated", weights editable in Settings."""

import pytest
from fastapi.testclient import TestClient

from app import db
from app.diagnostics import scoring
from app.diagnostics.registry import coverage, get_checks, run_all
from app.models import Finding, Resource


def _finding(check_id: str, severity: str, rid: str) -> Finding:
    return Finding(
        id=f"{check_id}:{rid}", check_id=check_id, severity=severity, title="t", summary="s",
        resource_id=rid,
    )


def _host(rid: str, **props) -> Resource:
    base = {"connectionState": "connected", "ntpServers": ["pool.ntp.org"]}
    base.update(props)
    return Resource(id=rid, type="host", name=rid, source="vc01", properties=base)


W = {"critical": 40, "warning": 15, "info": 0}


def test_one_bad_host_of_four_costs_more_than_one_of_forty():
    f = [_finding("HOST_DISCONNECTED", "critical", "h1")]
    four = scoring.compute_health(f, {"HOST_DISCONNECTED": 4}, W)
    forty = scoring.compute_health(f, {"HOST_DISCONNECTED": 40}, W)
    assert four["score"] == 90  # 100 - 40 * 1/4
    assert forty["score"] == 99  # 100 - 40 * 1/40 = 99
    assert four["passed"] == 0 and four["findings"] == 1 and four["not_evaluated"] == 0


def test_worked_example_mixed_checks():
    # 2 of 4 hosts disconnected (critical): 40 * 0.5 = 20
    # 1 of 10 datastores high usage (warning): 15 * 0.1 = 1.5
    # 3 of 3 VMs powered off (warning): 15 * 1.0 = 15
    # info finding on 1 of 2: weight 0
    findings = [
        _finding("HOST_DISCONNECTED", "critical", "h1"),
        _finding("HOST_DISCONNECTED", "critical", "h2"),
        _finding("DATASTORE_HIGH_USAGE", "warning", "d1"),
        _finding("VM_POWERED_OFF", "warning", "v1"),
        _finding("VM_POWERED_OFF", "warning", "v2"),
        _finding("VM_POWERED_OFF", "warning", "v3"),
        _finding("SOME_INFO", "info", "x"),
    ]
    cov = {
        "HOST_DISCONNECTED": 4,
        "DATASTORE_HIGH_USAGE": 10,
        "VM_POWERED_OFF": 3,
        "SOME_INFO": 2,
        "HOST_NTP_NOT_CONFIGURED": 4,  # evaluated, clean -> passed
        "NETWORK_REMOVED": 0,  # nothing to judge -> not evaluated
    }
    h = scoring.compute_health(findings, cov, W)
    assert h["score"] == round(100 - 20 - 1.5 - 15)  # 64 (63.5 rounds half to even = 64)
    assert h["passed"] == 1
    assert h["findings"] == 4
    assert h["not_evaluated"] == 1
    by = {c["check_id"]: c for c in h["checks"]}
    assert by["HOST_DISCONNECTED"]["deduction"] == 20.0
    assert by["DATASTORE_HIGH_USAGE"]["deduction"] == 1.5
    assert by["SOME_INFO"]["deduction"] == 0.0
    assert h["checks"][0]["check_id"] == "HOST_DISCONNECTED"  # worst first


def test_floor_at_zero():
    findings = [_finding(f"C{i}", "critical", "r") for i in range(5)]
    cov = {f"C{i}": 1 for i in range(5)}
    assert scoring.compute_health(findings, cov, W)["score"] == 0


def test_findings_without_coverage_still_count_and_are_capped():
    # A check reporting findings with no applicable() count: denominator is the
    # finding count, so the deduction is the full weight, never more.
    findings = [_finding("X", "critical", "a"), _finding("X", "critical", "b")]
    h = scoring.compute_health(findings, {}, W)
    assert h["score"] == 60
    assert h["findings"] == 1 and h["not_evaluated"] == 0


def test_not_evaluated_is_not_passed():
    h = scoring.compute_health([], {"A": 3, "B": 0, "C": 0}, W)
    assert h["score"] == 100
    assert (h["passed"], h["findings"], h["not_evaluated"]) == (1, 0, 2)


def test_weights_override_moves_score():
    f = [_finding("HOST_DISCONNECTED", "critical", "h1")]
    cov = {"HOST_DISCONNECTED": 2}
    assert scoring.compute_health(f, cov, {"critical": 40})["score"] == 80
    assert scoring.compute_health(f, cov, {"critical": 100})["score"] == 50
    assert scoring.compute_health(f, cov, {"critical": 0})["score"] == 100


def test_coverage_marks_previous_snapshot_checks_not_evaluated():
    hosts = [_host("h1"), _host("h2", ntpServers=None)]
    cov = coverage(hosts, None)
    assert cov["HOST_DISCONNECTED"] == 2
    assert cov["HOST_NTP_NOT_CONFIGURED"] == 1  # h2 did not report an NTP list
    assert cov["NETWORK_REMOVED"] == 0
    assert cov["RESOURCE_REMOVED"] == 0
    assert cov["CLUSTER_HOST_COUNT_CHANGE"] == 0
    assert cov["VM_POWERED_OFF"] == 0
    # With a previous snapshot the removed checks judge the previous objects.
    net = Resource(id="n1", type="network", name="n1", source="vc01")
    cov2 = coverage(hosts, [net, hosts[0], hosts[1]])
    assert cov2["NETWORK_REMOVED"] == 1
    assert cov2["RESOURCE_REMOVED"] == 2


def test_every_check_has_coverage_and_findings_never_exceed_it():
    from app.collectors.fixture import FixtureCollector

    resources = FixtureCollector("c", 0).collect()
    previous = FixtureCollector("c", 1).collect()
    for prev in (None, previous):
        cov = coverage(resources, prev)
        assert set(cov) == {c.id for c in get_checks()}
        per_check: dict[str, int] = {}
        for f in run_all(resources, prev):
            per_check[f.check_id] = per_check.get(f.check_id, 0) + 1
        for check_id, n in per_check.items():
            assert n <= cov[check_id], check_id


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("VCF_DOCTOR_HEALTH_WEIGHTS", raising=False)
    db.reset_for_tests(str(tmp_path / "t.db"))
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_weights_settings_roundtrip_and_reset(client):
    r = client.get("/api/settings/health-score")
    assert r.status_code == 200
    assert r.json()["weights"] == {"critical": 40, "warning": 15, "info": 0}
    assert "formula" in r.json()
    r = client.put("/api/settings/health-score", json={"weights": {"critical": 60}})
    assert r.status_code == 200, r.text
    assert r.json()["weights"] == {"critical": 60, "warning": 15, "info": 0}
    assert client.get("/api/settings/health-score").json()["weights"]["critical"] == 60
    bad_values = (
        {"critical": -1}, {"critical": 101}, {"critical": 1.5}, {"bogus": 1}, {"warning": True}
    )
    for bad in bad_values:
        r = client.put("/api/settings/health-score", json={"weights": bad})
        assert r.status_code == 400, bad
    assert client.put("/api/settings/health-score", json={"weights": "x"}).status_code == 400
    r = client.post("/api/settings/health-score/reset")
    assert r.json()["weights"] == {"critical": 40, "warning": 15, "info": 0}


def test_overview_reports_three_counts_and_weights_move_score(client):
    r = client.post(
        "/api/connections",
        json={"name": "fx", "host": "fixture", "username": "u", "password": "p", "kind": "fixture"},
    )
    assert r.status_code in (200, 201), r.text
    cid = r.json()["id"]
    client.post("/api/scan", json={"connection_id": cid})
    o = client.get(f"/api/overview?connection_id={cid}").json()
    h = o["health"]
    assert o["health_score"] == h["score"]
    assert 0 <= h["score"] <= 100
    assert h["passed"] + h["findings"] + h["not_evaluated"] == len(get_checks())
    assert o["counts"]["passed"] == h["passed"]
    # No previous snapshot yet: the removed/changed checks are not evaluated.
    assert h["not_evaluated"] >= 3
    if h["findings"] == 0:
        pytest.skip("fixture produced no findings; weights cannot move the score")
    client.put("/api/settings/health-score", json={"weights": {"critical": 0, "warning": 0}})
    assert client.get(f"/api/overview?connection_id={cid}").json()["health_score"] == 100
    client.put("/api/settings/health-score", json={"weights": {"critical": 100, "warning": 100}})
    assert client.get(f"/api/overview?connection_id={cid}").json()["health_score"] < h["score"]


def test_env_seed_for_defaults(monkeypatch, tmp_path):
    db.reset_for_tests(str(tmp_path / "e.db"))
    monkeypatch.setenv("VCF_DOCTOR_HEALTH_WEIGHTS", "critical=50, warning=bad, nope=3")
    assert scoring.default_weights() == {"critical": 50, "warning": 15, "info": 0}
    assert scoring.get_weights()["critical"] == 50
    # Stored weights win over the env seed; reset falls back to the env seed.
    scoring.set_weights({"critical": 10})
    assert scoring.get_weights()["critical"] == 10
    assert scoring.reset_weights()["critical"] == 50
