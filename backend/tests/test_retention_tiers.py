"""Tiered retention: selection per tier over synthetic timestamps."""

import gzip
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app import db
from app.models import ConnectionCreate
from app.models.snapshot import RetentionPolicy
from app.snapshots import store

AT = datetime(2026, 1, 21, 0, 0, tzinfo=UTC)  # midnight UTC keeps the day marks obvious
# Day marks are local midnights in the policy's timezone (issue #28), so tests
# that assert on them pin the zone instead of inheriting the machine's.
UTC_ZONE = "UTC"
EMPTY = gzip.compress(b"[]")


def _conn():
    return store.create_connection(
        ConnectionCreate(name="c", host="fixture", username="u", password="p", kind="fixture")
    )


def _bulk_insert(connection_id: str, stamps: list[datetime], scheduled: bool = True) -> list[str]:
    ids = [store.new_id() for _ in stamps]
    with db.transaction() as c:
        c.executemany(
            "INSERT INTO snapshots(id, connection_id, created_at, label, scheduled, "
            "resource_count, resources, resources_gz) VALUES(?,?,?,?,?,0,NULL,?)",
            [
                (sid, connection_id, t.isoformat(), f"S {t:%Y-%m-%d %H:%M}", int(scheduled), EMPTY)
                for sid, t in zip(ids, stamps, strict=True)
            ],
        )
    return ids


def _ages(connection_id: str) -> list[timedelta]:
    rows = db.fetchall(
        "SELECT created_at FROM snapshots WHERE connection_id = ? AND scheduled = 1",
        (connection_id,),
    )
    return [AT - store._dt(r["created_at"]) for r in rows]


def test_twenty_days_of_five_minute_snapshots(tmp_path):
    """Default policy against 20 days at a 5-minute cadence (5760 rows):
    everything under 14 days stays (4032), the 14..20 day band collapses to one
    per hour mark (145 marks, both ends inclusive), manual snapshots survive."""
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    stamps = [AT - timedelta(minutes=5 * k) for k in range(20 * 288)]
    _bulk_insert(conn.id, stamps)
    manual = _bulk_insert(conn.id, [AT - timedelta(days=19), AT - timedelta(days=2)], False)
    policy = RetentionPolicy(
        recent_days=14, hourly_days=30, daily_days=365, timezone=UTC_ZONE
    )

    deleted = store.apply_retention(conn.id, policy, at=AT)

    ages = _ages(conn.id)
    recent = [a for a in ages if a < timedelta(days=14)]
    hourly = [a for a in ages if a >= timedelta(days=14)]
    assert len(recent) == 4032
    assert len(hourly) == 145
    assert deleted == 5760 - 4032 - 145
    # Every survivor in the hourly band sits exactly on an hour mark, except
    # the 20-day mark itself, whose only candidates are just younger than it.
    hourly.sort()
    assert hourly[-1] == timedelta(days=20) - timedelta(minutes=5)
    assert all(a.total_seconds() % 3600 == 0 for a in hourly[:-1])
    survivors = {r["id"] for r in db.fetchall("SELECT id FROM snapshots")}
    assert set(manual) <= survivors
    # Idempotent: a second pass finds nothing to do.
    assert store.apply_retention(conn.id, policy, at=AT) == 0


def test_daily_tier_and_expiry(tmp_path):
    """Hourly cadence over 20 days with a 1/3/10 policy: 24 recent, 48 hourly,
    one per day mark in the 3..10 day band, nothing at 10 days or older."""
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    _bulk_insert(conn.id, [AT - timedelta(hours=k) for k in range(20 * 24)])
    policy = RetentionPolicy(recent_days=1, hourly_days=3, daily_days=10, timezone=UTC_ZONE)

    store.apply_retention(conn.id, policy, at=AT)

    ages = sorted(_ages(conn.id))
    assert len([a for a in ages if a < timedelta(days=1)]) == 24
    assert len([a for a in ages if timedelta(days=1) <= a < timedelta(days=3)]) == 48
    daily = [a for a in ages if a >= timedelta(days=3)]
    assert max(daily) < timedelta(days=10)
    # Day marks 3..9 each keep their exact-midnight snapshot; the last band
    # (9.5 .. 10 days) rounds to the 10-day mark and keeps its nearest, 239 h.
    assert [a.total_seconds() / 3600 for a in daily] == [72, 96, 120, 144, 168, 192, 216, 239]


def test_nearest_to_mark_wins_deterministically(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    base = AT - timedelta(days=20)  # hourly band under the default policy
    # 10:00 is exact; 09:55 and 10:05 are equally near an hour later? No: they
    # are 5 min from 10:00, so 10:00 wins. 11:05 and 11:50 have no exact mark;
    # 11:05 is nearer 11:00 than 11:50 is to 12:00, and each is its own group.
    stamps = {
        "exact": base.replace(hour=10, minute=0),
        "minus5": base.replace(hour=9, minute=55),
        "plus5": base.replace(hour=10, minute=5),
        "eleven05": base.replace(hour=11, minute=5),
        "eleven50": base.replace(hour=11, minute=50),
    }
    ids = dict(zip(stamps, _bulk_insert(conn.id, list(stamps.values())), strict=True))
    store.apply_retention(conn.id, at=AT)
    survivors = {r["id"] for r in db.fetchall("SELECT id FROM snapshots")}
    assert survivors == {ids["exact"], ids["eleven05"], ids["eleven50"]}


def test_tie_breaks_prefer_the_older_snapshot():
    policy = RetentionPolicy(recent_days=1, hourly_days=30, daily_days=365, timezone=UTC_ZONE)
    mark = AT - timedelta(days=5)
    rows = [("younger", mark + timedelta(minutes=10)), ("older", mark - timedelta(minutes=10))]
    assert store.select_retention_victims(rows, policy, AT) == ["younger"]
    assert store.select_retention_victims(list(reversed(rows)), policy, AT) == ["younger"]


def test_summary_tier_is_computed_from_age(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    now = store.now()
    ids = _bulk_insert(
        conn.id,
        [now - timedelta(days=7), now - timedelta(days=20), now - timedelta(days=40)],
    )
    manual = _bulk_insert(conn.id, [now - timedelta(days=40)], scheduled=False)[0]
    tiers = {s.id: s.tier for s in store.list_snapshots(conn.id)}
    assert tiers == {ids[0]: "recent", ids[1]: "hourly", ids[2]: "daily", manual: "manual"}
    assert store.get_snapshot(manual).tier == "manual"
    assert store.get_snapshot(ids[2]).tier == "daily"


def test_manual_snapshots_never_pruned_even_when_ancient(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    manual = _bulk_insert(conn.id, [AT - timedelta(days=3000)], scheduled=False)
    sched = _bulk_insert(conn.id, [AT - timedelta(days=3000)])
    assert store.apply_retention(conn.id, at=AT) == 1
    survivors = {r["id"] for r in db.fetchall("SELECT id FROM snapshots")}
    assert survivors == set(manual) and not survivors & set(sched)


def _kept(connection_id: str) -> list[datetime]:
    return sorted(
        store._dt(r["created_at"])
        for r in db.fetchall(
            "SELECT created_at FROM snapshots WHERE connection_id = ?", (connection_id,)
        )
    )


def test_day_marks_follow_the_policy_timezone(tmp_path):
    """The daily tier keeps the snapshot nearest local midnight, not 00:00 UTC.

    US operators saw the kept daily snapshot filed under the previous day on the
    Snapshots page, which groups by local calendar day (issue #28). Chicago is
    UTC-6 in January, so each day's survivor sits at 06:00 UTC. The newest
    snapshot belongs to the next day's mark, the same boundary the hourly tier
    has, so it is excluded from the comparison.
    """
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    # Three days of hourly snapshots inside the daily band of a 1/1/30 policy.
    stamps = [AT - timedelta(days=4) + timedelta(hours=k) for k in range(72)]
    _bulk_insert(conn.id, stamps)

    store.apply_retention(
        conn.id,
        RetentionPolicy(recent_days=1, hourly_days=1, daily_days=30, timezone="America/Chicago"),
        at=AT,
    )

    chicago = _kept(conn.id)
    assert [t.hour for t in chicago[:-1]] == [6, 6, 6]
    assert all(t.astimezone(ZoneInfo("America/Chicago")).hour == 0 for t in chicago[:-1])

    # The same estate under UTC keeps midnight UTC instead: the old behaviour.
    db.reset_for_tests(str(tmp_path / "u.db"))
    conn = _conn()
    _bulk_insert(conn.id, stamps)
    store.apply_retention(
        conn.id,
        RetentionPolicy(recent_days=1, hourly_days=1, daily_days=30, timezone=UTC_ZONE),
        at=AT,
    )
    assert [t.hour for t in _kept(conn.id)[:-1]] == [0, 0, 0]


def test_day_marks_survive_a_dst_change(tmp_path):
    """A 23-hour local day (spring forward) still keeps one snapshot per local day."""
    db.reset_for_tests(str(tmp_path / "t.db"))
    conn = _conn()
    at = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
    tz = ZoneInfo("America/Chicago")
    # 2026-03-08 is the US spring-forward day; cover the days around it hourly.
    start = datetime(2026, 3, 6, 0, 0, tzinfo=UTC)
    _bulk_insert(conn.id, [start + timedelta(hours=k) for k in range(24 * 5)])

    store.apply_retention(
        conn.id,
        RetentionPolicy(recent_days=1, hourly_days=1, daily_days=90, timezone="America/Chicago"),
        at=at,
    )

    # Every survivor but the newest (which belongs to the following day's mark)
    # sits exactly on a local midnight, one per local calendar day.
    kept = _kept(conn.id)
    local = [t.astimezone(tz) for t in kept[:-1]]
    assert all((t.hour, t.minute) == (0, 0) for t in local), local
    assert len({t.date() for t in local}) == len(local) >= 4


def test_day_mark_midpoint_uses_the_real_day_length_under_dst():
    """New York's fall-back day is 25 hours long, so its midpoint is 12:30 after
    local midnight: a snapshot at 12:20 rounds down and one at 12:40 rounds up."""
    tz = ZoneInfo("America/New_York")
    midnight = datetime(2026, 11, 1, tzinfo=tz)
    next_midnight = datetime(2026, 11, 2, tzinfo=tz)
    assert next_midnight.astimezone(UTC) - midnight.astimezone(UTC) == timedelta(hours=25)
    before = (midnight.astimezone(UTC) + timedelta(hours=12, minutes=20)).astimezone(UTC)
    after = (midnight.astimezone(UTC) + timedelta(hours=12, minutes=40)).astimezone(UTC)
    assert store._nearest_day_mark(before, tz) == midnight
    assert store._nearest_day_mark(after, tz) == next_midnight
