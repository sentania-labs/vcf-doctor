"""events table: dedup, filters, pruning, and the capture service."""

from datetime import UTC, datetime, timedelta

import pytest

from app import db
from app.events import service
from app.events import store as events_store
from app.models import Resource
from app.models.event import Event
from app.models.snapshot import Snapshot

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    db.reset_for_tests(str(tmp_path / "events.db"))
    yield


def _ev(key: str, minutes_ago: int, conn="c1", **kw) -> Event:
    fields = dict(
        id=f"{conn}:{key}",
        connection_id=conn,
        time=NOW - timedelta(minutes=minutes_ago),
        type="VmPoweredOffEvent",
        category="info",
        message=f"event {key}",
    )
    fields.update(kw)
    return Event(**fields)


def test_upsert_dedups_on_id():
    first = [_ev("1", 5), _ev("2", 4)]
    assert events_store.upsert_events(first) == 2
    again = [_ev("1", 5, message="changed"), _ev("3", 3)]
    assert events_store.upsert_events(again) == 1
    rows = events_store.list_events("c1", since=NOW - timedelta(hours=1))
    assert [r.id for r in rows] == ["c1:3", "c1:2", "c1:1"]  # newest first
    assert rows[-1].message == "event 1"  # first write wins
    assert events_store.count_events("c1") == 3
    assert events_store.upsert_events([]) == 0


def test_filters_category_resource_q_window_and_limit():
    events_store.upsert_events(
        [
            _ev(
                "a",
                10,
                category="user",
                user="Administrator@vsphere.local",
                resource_id="vm:c1:web03",
                resource_name="web03",
                message="web03 is powered off",
            ),
            _ev(
                "b",
                20,
                category="error",
                resource_id="host:c1:esx03",
                resource_name="esx03.wld01.vcf.example",
                message="Host not responding",
            ),
            _ev("c", 30, category="user", user="netops@x", message="VLAN 200 -> 201"),
            _ev("d", 90, category="info", message="old"),
            _ev("e", 5, conn="c2", category="error", message="other connection"),
        ]
    )
    since = NOW - timedelta(hours=1)
    assert [e.id for e in events_store.list_events("c1", since=since)] == ["c1:a", "c1:b", "c1:c"]
    assert [e.id for e in events_store.list_events("c1", since=since, category="user")] == [
        "c1:a",
        "c1:c",
    ]
    assert [e.id for e in events_store.list_events("c1", resource_id="vm:c1:web03")] == ["c1:a"]
    # q is case-insensitive over message, user and resource_name
    assert [e.id for e in events_store.list_events("c1", q="ADMINISTRATOR")] == ["c1:a"]
    assert [e.id for e in events_store.list_events("c1", q="esx03.wld01")] == ["c1:b"]
    assert [e.id for e in events_store.list_events("c1", q="vlan")] == ["c1:c"]
    assert [e.id for e in events_store.list_events("c1", q="nomatch")] == []
    # until bounds the top of the window; limit trims the newest-first list
    assert [e.id for e in events_store.list_events("c1", until=NOW - timedelta(minutes=15))] == [
        "c1:b",
        "c1:c",
        "c1:d",
    ]
    assert len(events_store.list_events("c1", limit=2)) == 2
    assert len(events_store.list_events()) == 5  # all connections


def test_prune_and_delete():
    events_store.upsert_events(
        [
            _ev("fresh", 60),
            _ev("old", 60 * 24 * 40),
            _ev("older", 60 * 24 * 400),
            _ev("other", 60 * 24 * 400, conn="c2"),
        ]
    )
    assert events_store.prune_events("c1", 365, now=NOW) == 1
    assert events_store.prune_events("c1", 30, now=NOW) == 1
    assert events_store.prune_events("c1", 30, now=NOW) == 0
    assert events_store.count_events("c1") == 1
    assert events_store.count_events("c2") == 1  # other connections untouched
    assert events_store.delete_events("c2") == 1
    assert events_store.latest_event_time("c1") == NOW - timedelta(minutes=60)
    assert events_store.latest_event_time("c2") is None


def test_retention_days_reads_policy_with_fallback():
    assert service.retention_days() == service.DEFAULT_RETENTION_DAYS
    db.set_setting("retention_policy", {"recent_days": 14, "hourly_days": 30, "daily_days": 90})
    assert service.retention_days() == 90
    db.set_setting("retention_policy", {"daily_days": 0})
    assert service.retention_days() == service.DEFAULT_RETENTION_DAYS
    db.set_setting("retention_policy", "garbage")
    assert service.retention_days() == service.DEFAULT_RETENTION_DAYS


def _snapshot(sid: str, created: datetime, conn="c1") -> Snapshot:
    return Snapshot(
        id=sid,
        created_at=created,
        label=sid,
        connection_id=conn,
        resources=[
            Resource(id="vm:c1:vm-13", type="vm", name="web03", source="vcenter:c1"),
            Resource(id="host:c1:host-3", type="host", name="esx03", source="vcenter:c1"),
        ],
    )


class FakeCollector:
    def __init__(self, events):
        self.events = events
        self.calls: list[tuple[datetime, datetime]] = []

    def collect_events(self, since, until):
        self.calls.append((since, until))
        return self.events


class NoEventsCollector:
    pass


def test_capture_window_first_scan_then_overlap(monkeypatch):
    from app.snapshots import store

    snaps = {}

    def fake_list(conn_id):
        return [s for s in snaps.values() if s.connection_id == conn_id]

    monkeypatch.setattr(store, "list_snapshots", fake_list)
    s1 = _snapshot("s1", NOW - timedelta(minutes=15))
    snaps["s1"] = s1
    since, until = service.capture_window("c1", s1)
    assert until == s1.created_at and since == until - timedelta(hours=24)
    s2 = _snapshot("s2", NOW)
    snaps["s2"] = s2
    since, until = service.capture_window("c1", s2)
    assert until == NOW and since == s1.created_at - timedelta(seconds=60)


def test_capture_events_enriches_stores_prunes_and_never_raises(monkeypatch):
    from app.snapshots import store

    snap = _snapshot("s1", NOW)
    monkeypatch.setattr(store, "list_snapshots", lambda cid: [snap])
    db.set_setting("retention_policy", {"daily_days": 30})
    collector = FakeCollector(
        [
            Event(
                id="c1:1",
                connection_id="c1",
                time=NOW,
                type="VmPoweredOffEvent",
                category="user",
                message="off",
                resource_id="vm:c1:vm-13",
            ),
            Event(
                id="c1:2",
                connection_id="c1",
                time=NOW,
                type="X",
                message="unknown entity",
                resource_id="vm:c1:vm-99",
            ),
            Event(
                id="c1:3",
                connection_id="c1",
                time=NOW - timedelta(days=40),
                type="Old",
                message="pruned right away",
            ),
        ]
    )
    conn = type("Conn", (), {"id": "c1"})()
    assert service.capture_events(conn, collector, snap) == 2  # c1:3 is past retention
    assert collector.calls == [(NOW - timedelta(hours=24), NOW)]
    rows = {e.id: e for e in events_store.list_events("c1", since=NOW - timedelta(days=365))}
    assert set(rows) == {"c1:1", "c1:2"}  # c1:3 never stored under the 30 day policy
    assert rows["c1:1"].resource_name == "web03" and rows["c1:1"].resource_type == "vm"
    assert rows["c1:2"].resource_name is None  # not in the snapshot: id kept, no name
    # second capture of the same window is a no-op thanks to dedup
    assert service.capture_events(conn, collector, snap) == 0
    # collectors without the hook and collectors that blow up are both harmless
    assert service.capture_events(conn, NoEventsCollector(), snap) == 0

    class Boom:
        def collect_events(self, since, until):
            raise RuntimeError("vCenter said no")

    assert service.capture_events(conn, Boom(), snap) == 0
