"""Regression tests for the pre-PR critic review findings."""

from pathlib import Path

from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch, static: Path | None = None):
    from app import db
    from app.config import settings

    db.reset_for_tests(str(tmp_path / "t.db"))
    monkeypatch.setattr(settings, "demo_mode", True)
    if static is not None:
        monkeypatch.setattr(settings, "static_dir", str(static))
    import importlib

    import app.main as main

    importlib.reload(main)
    return TestClient(main.app)


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
    with _client(tmp_path, monkeypatch) as c:
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


def test_demo_flow_produces_known_findings_and_changes(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        a = c.get("/api/connections").json()[0]["id"]
        c.post("/api/scan", json={"connection_id": a})
        findings = c.get(f"/api/findings?connection_id={a}").json()
        checks = {f["check_id"] for f in findings}
        assert {"HOST_DISCONNECTED", "NETWORK_REMOVED", "DATASTORE_HIGH_USAGE"} <= checks
        assert "HOST_COUNT_LOW" not in checks
        changes = c.get(f"/api/changes?connection_id={a}").json()
        assert len(changes) == 8
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
