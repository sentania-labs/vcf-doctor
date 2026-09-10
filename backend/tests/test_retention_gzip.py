"""Gzip storage of snapshot resources."""

import gzip
import json

from app import db
from app.collectors.fixture import load_fixture
from app.models import ConnectionCreate
from app.snapshots import store


def _conn():
    return store.create_connection(
        ConnectionCreate(name="c", host="fixture", username="u", password="p", kind="fixture")
    )


def test_gzip_roundtrip_and_ratio():
    db.reset_for_tests()
    conn = _conn()
    resources = load_fixture("snapshot_a.json")
    snap = store.save_snapshot(conn.id, resources, "Manual", scheduled=False)
    row = db.fetchone("SELECT resources_gz FROM snapshots WHERE id = %s", (snap.id,))
    blob = bytes(row["resources_gz"])
    plain = len(json.dumps([r.model_dump(mode="json") for r in resources]).encode())
    assert plain / len(blob) > 5, "expected at least 5x on the fixture"
    assert json.loads(gzip.decompress(blob)) == [r.model_dump(mode="json") for r in resources]
    assert store.get_snapshot(snap.id).resources == resources


def test_snapshot_survives_a_restart():
    """The resource blob is durable, so a restart still serves old snapshots."""
    db.reset_for_tests()
    conn = _conn()
    resources = load_fixture("snapshot_a.json")
    snap = store.save_snapshot(conn.id, resources, "Manual", scheduled=False)
    db.reconnect_for_tests()
    assert store.get_snapshot(snap.id).resources == resources
