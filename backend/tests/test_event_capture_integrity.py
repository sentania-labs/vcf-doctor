"""Event capture checkpoints, cap recovery, row limits, and bounded cleanup."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import db
from app.events import service
from app.events import store as events_store
from app.models.event import Event, EventPolicy
from app.models.snapshot import Snapshot

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    db.reset_for_tests()


def _snapshot(at: datetime = NOW) -> Snapshot:
    return Snapshot(id="s", connection_id="c1", created_at=at, label="scan", resources=[])


def _event(key: int, at: datetime, message: str = "event") -> Event:
    return Event(
        id=f"c1:{key}",
        connection_id="c1",
        time=at,
        type="SyntheticEvent",
        message=message,
    )


def test_failed_fetch_keeps_checkpoint_and_next_scan_retries_gap():
    events_store.set_event_policy(EventPolicy(retention_hours=2, row_cap=250_000))
    checkpoint = NOW - timedelta(minutes=30)
    events_store.set_capture_checkpoint("c1", checkpoint)

    class Flaky:
        def __init__(self):
            self.calls = []
            self.fail = True

        def collect_events(self, since, until):
            self.calls.append((since, until))
            if self.fail:
                raise RuntimeError("temporary vCenter failure")
            return [_event(1, checkpoint + timedelta(minutes=2))]

    collector = Flaky()
    connection = SimpleNamespace(id="c1")
    assert service.capture_events(connection, collector, _snapshot()) == 0
    assert events_store.capture_checkpoint("c1") == checkpoint

    collector.fail = False
    next_scan = NOW + timedelta(minutes=5)
    assert service.capture_events(connection, collector, _snapshot(next_scan)) == 1
    assert collector.calls[-1][0] == checkpoint - service.OVERLAP
    assert events_store.capture_checkpoint("c1") == next_scan


def test_capped_high_volume_window_is_split_and_deduplicated(monkeypatch):
    monkeypatch.setattr(service, "MAX_ITEMS", 10)
    events_store.set_event_policy(EventPolicy(retention_hours=2, row_cap=250_000))
    rows = [_event(i, NOW - timedelta(seconds=i * 30)) for i in range(100)]

    class Capped:
        def __init__(self):
            self.calls = []

        def collect_events(self, since, until):
            self.calls.append((since, until))
            matching = [e for e in rows if since < e.time <= until]
            return matching[: service.MAX_ITEMS]

    collector = Capped()
    assert service.capture_events(SimpleNamespace(id="c1"), collector, _snapshot()) == 100
    assert len(collector.calls) > 1
    assert events_store.count_events("c1") == 100
    assert events_store.capture_status("c1").incomplete_intervals == []

    overlap = _snapshot(NOW + timedelta(minutes=5))
    assert service.capture_events(SimpleNamespace(id="c1"), collector, overlap) == 0
    assert events_store.count_events("c1") == 100


def test_minimum_capped_interval_is_recorded_then_retried(monkeypatch):
    monkeypatch.setattr(service, "MAX_ITEMS", 10)
    monkeypatch.setattr(service, "MIN_WINDOW", timedelta(minutes=5))
    events_store.set_event_policy(EventPolicy(retention_hours=1, row_cap=250_000))
    rows = [_event(i, NOW - timedelta(minutes=10)) for i in range(12)]

    class Dense:
        complete = False

        def collect_events(self, since, until):
            matching = [e for e in rows if since <= e.time <= until]
            if self.complete:
                return SimpleNamespace(events=matching, complete=True)
            return matching[: service.MAX_ITEMS]

    collector = Dense()
    connection = SimpleNamespace(id="c1")
    assert service.capture_events(connection, collector, _snapshot()) == 10
    status = events_store.capture_status("c1")
    assert status.last_complete_end is None
    assert status.incomplete_intervals

    collector.complete = True
    assert service.capture_events(connection, collector, _snapshot(NOW + timedelta(minutes=1))) == 2
    status = events_store.capture_status("c1")
    assert status.last_complete_end == NOW + timedelta(minutes=1)
    assert status.incomplete_intervals == []
    assert events_store.count_events("c1") == 12


def test_independent_pruning_and_per_connection_row_cap():
    events_store.set_event_policy(EventPolicy(retention_hours=48, row_cap=1000))
    rows = [_event(i, NOW - timedelta(seconds=i)) for i in range(1005)]
    rows.append(_event(2000, NOW - timedelta(hours=49)))
    events_store.upsert_events(rows)

    assert events_store.prune_events("c1", 48, now=NOW) == 1
    assert events_store.enforce_row_cap("c1", 1000) == 5
    kept = events_store.list_events("c1", limit=5000)
    assert len(kept) == 1000
    assert {e.id for e in kept}.isdisjoint({"c1:1000", "c1:1001", "c1:1002", "c1:1003", "c1:1004"})


def test_persistent_burst_coalesces_gaps_without_extra_retries(monkeypatch):
    monkeypatch.setattr(service, "MAX_ITEMS", 10)
    events_store.set_capture_checkpoint("c1", NOW - timedelta(minutes=30))
    burst = NOW - timedelta(minutes=10, microseconds=123456)
    rows = [_event(i, burst) for i in range(12)]
    inserted_batches = []
    original_upsert = events_store.upsert_events

    def upsert(events):
        inserted_batches.append(len(events))
        return original_upsert(events)

    monkeypatch.setattr(events_store, "upsert_events", upsert)

    class Dense:
        def __init__(self):
            self.calls = []

        def collect_events(self, since, until):
            self.calls.append((since, until))
            return [e for e in rows if since <= e.time <= until][:10]

    collector = Dense()
    for i in range(40):
        collector.calls.clear()
        snapshot = _snapshot(NOW + timedelta(minutes=i))
        expected_window = service.capture_window("c1", snapshot)
        service.capture_events(SimpleNamespace(id="c1"), collector, snapshot)
        assert collector.calls[0] == expected_window
        assert len(collector.calls) < 35
        assert inserted_batches[-1] <= 20
        assert len(events_store.capture_status("c1").incomplete_intervals) == 1
    assert events_store.count_events("c1") == 10
    assert events_store.capture_checkpoint("c1") == NOW - timedelta(minutes=30)


def test_gap_coalescing_is_transitive_and_connection_scoped():
    def record(connection, start, end):
        events_store.record_incomplete_interval(
            connection, NOW + timedelta(seconds=start), NOW + timedelta(seconds=end), "capped"
        )

    record("c1", 0, 2)
    record("c1", 4, 6)
    record("c2", 1, 5)
    record("c1", 1, 5)
    gaps = events_store.capture_status("c1").incomplete_intervals
    assert len(gaps) == 1
    assert (gaps[0].since, gaps[0].until) == (NOW, NOW + timedelta(seconds=6))
    assert len(events_store.capture_status("c2").incomplete_intervals) == 1


def test_gap_outside_capture_window_is_still_retried():
    events_store.set_capture_checkpoint("c1", NOW - timedelta(minutes=5))
    start, end = NOW - timedelta(minutes=20), NOW - timedelta(minutes=19)
    events_store.record_incomplete_interval("c1", start, end, "failure")
    calls = []

    def collect(since, until):
        calls.append((since, until))
        return [_event(1, end)] if (since, until) == (start, end) else []

    assert service.capture_events(
        SimpleNamespace(id="c1"), SimpleNamespace(collect_events=collect), _snapshot()
    ) == 1
    assert calls[0] == (start, end)
    assert events_store.capture_status("c1").incomplete_intervals == []


def test_capture_state_survives_a_restart():
    """A checkpoint and the task-history flag are durable, so a restart resumes
    where capture left off instead of refetching from scratch."""
    events_store.set_capture_checkpoint("c1", NOW)
    db.reconnect_for_tests()
    status = events_store.capture_status("c1")
    assert status.last_complete_end == NOW
    assert status.task_history_unavailable is False
    events_store.set_task_history_unavailable("c1", True)
    db.reconnect_for_tests()
    status = events_store.capture_status("c1")
    assert status.task_history_unavailable is True
    assert status.last_complete_end == NOW


def test_outage_longer_than_retention_uses_one_bounded_query_per_scan():
    from app.collectors.vsphere.events import CaptureBatch

    events_store.set_event_policy(EventPolicy(retention_hours=48, row_cap=250_000))
    checkpoint = NOW - timedelta(minutes=5)
    events_store.set_capture_checkpoint("c1", checkpoint)
    calls = []
    failing = True

    def collect(since, until):
        calls.append((since, until))
        return CaptureBatch(
            events=[_event(1, until)],
            complete=not failing,
            error="task history fetch failed" if failing else None,
        )

    collector = SimpleNamespace(collect_events=collect)
    for hour in range(61):
        end = NOW + timedelta(hours=hour)
        cutoff = end - timedelta(hours=48)
        expected_start = max(checkpoint - service.OVERLAP, cutoff)
        calls.clear()
        service.capture_events(SimpleNamespace(id="c1"), collector, _snapshot(end))
        assert calls == [(expected_start, end)]
        status = events_store.capture_status("c1")
        assert status.last_complete_end == checkpoint
        assert len(status.incomplete_intervals) == 1
        gap = status.incomplete_intervals[0]
        assert (gap.since, gap.until) == (expected_start, end)

    failing = False
    end = NOW + timedelta(hours=61)
    calls.clear()
    service.capture_events(SimpleNamespace(id="c1"), collector, _snapshot(end))
    assert calls == [(end - timedelta(hours=48), end)]
    status = events_store.capture_status("c1")
    assert status.last_complete_end == end
    assert status.incomplete_intervals == []


def test_gap_pruning_trims_crossing_interval_and_preserves_other_connections():
    events_store.record_incomplete_interval(
        "c1", NOW - timedelta(hours=4), NOW - timedelta(hours=3), "expired"
    )
    for connection_id in ("c1", "c2"):
        events_store.record_incomplete_interval(
            connection_id, NOW - timedelta(hours=2), NOW, "task failure", at=NOW
        )
    original = events_store.capture_status("c1").incomplete_intervals[-1]
    cutoff = NOW - timedelta(hours=1)
    assert events_store.prune_incomplete_intervals("c1", cutoff) == 1
    gaps = events_store.capture_status("c1").incomplete_intervals
    assert len(gaps) == 1
    assert gaps[0] == original.model_copy(update={"since": cutoff})
    assert events_store.capture_status("c2").incomplete_intervals[0].since == (
        NOW - timedelta(hours=2)
    )
