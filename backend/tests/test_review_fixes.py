"""Regression tests for the pre-PR critic review findings."""

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import seed_fixture_connection


def _client(tmp_path, monkeypatch, static: Path | None = None):
    from app import db
    from app.config import settings

    db.reset_for_tests(str(tmp_path / "t.db"))
    if static is not None:
        monkeypatch.setattr(settings, "static_dir", str(static))
    import importlib

    import app.main as main

    importlib.reload(main)
    return TestClient(main.app)


def _seeded(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    c.__enter__()
    seed_fixture_connection(c)
    return c


def test_spa_catch_all_cannot_escape_static_dir(tmp_path, monkeypatch):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<title>VCF Doctor</title>")
    (tmp_path / "secret.db").write_text("sqlite")
    with _client(tmp_path, monkeypatch, static) as c:
        for path in ("/../secret.db", "/%2e%2e/secret.db", "/assets/../../secret.db"):
            r = c.get(path)
            assert "sqlite" not in r.text, path
        assert c.get("/api/does-not-exist").status_code == 404
        assert "<title>VCF Doctor</title>" in c.get("/health").text


def test_two_connections_are_isolated_and_namespaced(tmp_path, monkeypatch):
    with _seeded(tmp_path, monkeypatch) as c:
        a = c.get("/api/connections").json()[0]["id"]
        b = c.post(
            "/api/connections",
            json={
                "name": "Second WLD",
                "host": "fixture",
                "username": "u",
                "password": "p",
                "kind": "fixture",
            },
        ).json()["id"]
        c.post("/api/scan", json={"connection_id": b})
        ra = c.get(f"/api/resources?connection_id={a}").json()
        rb = c.get(f"/api/resources?connection_id={b}").json()
        assert len(ra) == 51 and len(rb) == 51
        ids_a = {r["id"] for r in ra}
        ids_b = {r["id"] for r in rb}
        assert ids_a.isdisjoint(ids_b), "resource ids collide across connections"
        assert all(f":{a}" in i for i in ids_a) and all(f":{b}" in i for i in ids_b)
        merged = c.get("/api/resources").json()
        assert len({r["id"] for r in merged}) == 102
        # parent and relationship targets were rewritten too
        for r in rb:
            if r["parent_id"]:
                assert r["parent_id"] in ids_b
            for rel in r["relationships"]:
                assert rel["target_id"] in ids_b
        # changes across connections is refused
        sa = c.get(f"/api/snapshots?connection_id={a}").json()[0]["id"]
        r = c.get(f"/api/changes?connection_id={b}&to={sa}")
        assert r.status_code == 400


def test_fixture_flow_produces_known_findings_and_changes(tmp_path, monkeypatch):
    with _seeded(tmp_path, monkeypatch) as c:
        a = c.get("/api/connections").json()[0]["id"]
        c.post("/api/scan", json={"connection_id": a})
        findings = c.get(f"/api/findings?connection_id={a}").json()
        checks = {f["check_id"] for f in findings}
        assert {
            "HOST_DISCONNECTED",
            "NETWORK_REMOVED",
            "DATASTORE_HIGH_USAGE",
            "HOST_MAINTENANCE_MODE",
            "VM_POWERED_OFF",
            "VM_SNAPSHOT_STALE",
        } <= checks
        assert "HOST_COUNT_LOW" not in checks
        # esx04 drops to one NTP server, which is still configured.
        assert "HOST_NTP_NOT_CONFIGURED" not in checks
        assert not {"CLUSTER_HA_DISABLED", "CLUSTER_DRS_DISABLED", "HOST_VERSION_MISMATCH"} & checks
        # VM_SNAPSHOT_STALE fires on both outliers: backup-proxy01 (4 snapshots)
        # and monitoring01 (one snapshot 21 days old at BASE_TIME).
        stale = {
            f["resource_id"].rsplit(":", 1)[-1]
            for f in findings
            if f["check_id"] == "VM_SNAPSHOT_STALE"
        }
        assert stale == {"backup-proxy01", "monitoring01"}
        changes = c.get(f"/api/changes?connection_id={a}").json()
        # One Change per changed resource: 14 modified (esx02, esx03, esx04,
        # esx07, pg-vmotion, wld01-edge, vsan01, app01, app02, db01, web02,
        # web03, dmz-lb01, dmz-jump01) plus the removed DMZ segment.
        assert len(changes) == 15
        by_sig = {}
        for ch in changes:
            by_sig[ch["significance"]] = by_sig.get(ch["significance"], 0) + 1
        assert by_sig == {"high": 4, "medium": 9, "low": 2}
        assert any(
            ch["resource_name"].startswith("esx03") and ch["significance"] == "high"
            for ch in changes
        )
        ov = c.get(f"/api/overview?connection_id={a}").json()
        assert ov["counts"]["critical"] == 2 and ov["health_score"] < 100


def test_interrupted_runs_are_reconciled_on_startup(tmp_path, monkeypatch):
    from app import db
    from app.snapshots import store

    db.reset_for_tests(str(tmp_path / "r.db"))
    db.connect()
    conn = store.create_connection(
        __import__("app.models", fromlist=["ConnectionCreate"]).ConnectionCreate(
            name="x", host="fixture", username="u", password="p", kind="fixture"
        )
    )
    run = store.create_run(conn.id, "manual")
    assert run.status == "running"
    assert store.reconcile_interrupted_runs() == 1
    assert store.get_run(run.id).status == "error"
    assert store.reconcile_interrupted_runs() == 0
