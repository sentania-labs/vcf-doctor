"""GET /api/events with the fixture collector: fixture events appear after the second scan."""

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from tests.conftest import seed_fixture_connection


@pytest.fixture()
def client(tmp_path):
    db.reset_for_tests(str(tmp_path / "fixture.db"))
    with TestClient(app) as c:
        seed_fixture_connection(c)
        yield c


def _conn_id(client) -> str:
    conns = client.get("/api/connections").json()
    assert len(conns) == 1 and conns[0]["kind"] == "fixture"
    return conns[0]["id"]


def test_first_scan_has_no_events_second_scan_loads_fixture(client):
    cid = _conn_id(client)
    assert client.get(f"/api/events?connection_id={cid}").json() == []
    r = client.post("/api/scan", json={"connection_id": cid})
    assert r.status_code == 200 and r.json()[0]["status"] == "ok"

    events = client.get(f"/api/events?connection_id={cid}").json()
    assert len(events) >= 25
    times = [e["time"] for e in events]
    assert times == sorted(times, reverse=True)  # newest first
    assert {e["source"] for e in events} == {"event", "task"}
    assert {e["category"] for e in events} == {"info", "warning", "error", "user"}
    assert all(e["connection_id"] == cid for e in events)
    assert all(e["id"].startswith(f"{cid}:") for e in events)
    # fixture resource ids were re-keyed onto the connection
    assert all((e["resource_id"] or f"x:{cid}:x").split(":")[1] == cid for e in events)

    users = client.get(f"/api/events?connection_id={cid}&category=user").json()
    assert users and all(e["category"] == "user" for e in users)
    assert "administrator@vsphere.local" in {e["user"] for e in users}

    web03 = client.get(f"/api/events?connection_id={cid}&resource_id=vm:{cid}:web03").json()
    types = [e["type"] for e in web03]
    assert "VmPoweredOffEvent" in types and "VirtualMachine.powerOff" in types
    assert all(e["resource_name"] == "web03" for e in web03)
    off = next(e for e in web03 if e["type"] == "VmPoweredOffEvent")
    assert off["user"] == "administrator@vsphere.local" and off["category"] == "user"

    errors = client.get(f"/api/events?connection_id={cid}&category=error").json()
    assert {e["resource_id"] for e in errors} == {f"host:{cid}:esx03"}

    q = client.get(f"/api/events?connection_id={cid}&q=VMOTION").json()
    assert q and all("vmotion" in (e["message"] + (e["resource_name"] or "")).lower() for e in q)

    # a third scan re-offers the same fixture events; dedup keeps the count stable
    client.post("/api/scan", json={"connection_id": cid})
    assert len(client.get(f"/api/events?connection_id={cid}&limit=1000").json()) == len(events)
    assert len(client.get(f"/api/events?connection_id={cid}&limit=5").json()) == 5


def test_validation_and_unscoped_listing(client):
    cid = _conn_id(client)
    client.post("/api/scan", json={"connection_id": cid})
    assert client.get("/api/events?connection_id=nope").status_code == 404
    assert client.get(f"/api/events?connection_id={cid}&category=loud").status_code == 422
    assert client.get("/api/events?limit=0").status_code == 422
    everything = client.get("/api/events").json()
    assert len(everything) == len(client.get(f"/api/events?connection_id={cid}").json())
    # explicit window: since far in the future returns nothing
    assert client.get("/api/events?since=2999-01-01T00:00:00Z").json() == []
    assert len(client.get("/api/events?since=2000-01-01T00:00:00Z").json()) == len(everything)
