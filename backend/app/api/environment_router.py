"""GET /api/environment/changes: what changed across the whole estate between
two points in time, rolled up over every connection.

Reads only persisted data: the change log each scan writes (see
/api/changes/log) and the findings cached with each snapshot. It never
recomputes a diff from snapshot contents, so it stays cheap however many
vCenters are attached.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.router import _parse_time, _resolve_min_significance
from app.models import Finding
from app.models.change import ChangeRecord
from app.snapshots import store

router = APIRouter(prefix="/api/environment", tags=["environment"])

DEFAULT_PER_CONNECTION = 500
MAX_PER_CONNECTION = 5000


class SignificanceCounts(BaseModel):
    high: int = 0
    medium: int = 0
    low: int = 0
    total: int = 0


class FindingsDelta(BaseModel):
    """Findings present on the end snapshot but not on the baseline (appeared),
    and the reverse (cleared), keyed by the deterministic finding id. The
    baseline is the newest snapshot before the window, or the oldest one inside
    it when nothing earlier exists; the end is the newest snapshot inside it."""

    baseline_snapshot_id: str
    baseline_at: datetime
    end_snapshot_id: str
    end_at: datetime
    appeared: list[Finding] = Field(default_factory=list)
    cleared: list[Finding] = Field(default_factory=list)


class ConnectionWindow(BaseModel):
    connection_id: str
    name: str
    host: str
    kind: str
    has_data: bool  # at least one snapshot or change row observed inside the window
    snapshots_in_window: int
    counts: SignificanceCounts
    changes: list[ChangeRecord]
    truncated: bool  # per-connection limit reached; the counts are still complete
    findings: FindingsDelta | None  # None when there are not two snapshots to compare


class EnvironmentTotals(BaseModel):
    connections: int
    covered: int
    no_data: int
    changes: SignificanceCounts
    findings_appeared: int
    findings_cleared: int


class EnvironmentChanges(BaseModel):
    since: datetime
    until: datetime
    window: str  # "last_cycle" when since was derived, else "custom"
    min_significance: str
    totals: EnvironmentTotals
    connections: list[ConnectionWindow]


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def last_cycle_since(until: datetime) -> datetime | None:
    """Start of the most recent scan cycle: the oldest of each connection's
    newest snapshot at or before `until`, so every connection's latest scan
    falls inside [since, until]. Connections whose schedule is paused do not
    set the start (a month-old paused connection would drag the window back
    for everyone); they are only considered when nothing is scheduled at all.
    None when nothing has been scanned yet."""
    scheduled: list[datetime] = []
    paused: list[datetime] = []
    for conn in store.list_connections():
        snap = store.snapshot_summary_at(conn.id, at_or_before=until)
        if snap is None:
            continue
        sched = store.get_schedule(conn.id)
        (scheduled if sched is not None and sched.enabled else paused).append(snap.created_at)
    latest = scheduled or paused
    return min(latest) if latest else None


def _at_or_above(floor: str) -> tuple[str, ...]:
    order = ("high", "medium", "low")
    return order[: order.index(floor) + 1]


def _findings_delta(connection_id: str, since: datetime, until: datetime) -> FindingsDelta | None:
    """Baseline: the newest snapshot before the window (or, failing that, the
    oldest inside it). End: the newest snapshot inside the window."""
    end = store.snapshot_summary_at(connection_id, at_or_before=until)
    if end is None or end.created_at < since:
        return None
    baseline = store.snapshot_summary_at(connection_id, before=since)
    if baseline is None:
        baseline = store.earliest_snapshot_summary_since(connection_id, since, until)
    if baseline is None or baseline.id == end.id:
        return None
    # A snapshot without a findings row (older than the cache) would make every
    # current finding look new; say nothing rather than something wrong.
    if not (store.findings_cached(baseline.id) and store.findings_cached(end.id)):
        return None
    before = {f.id: f for f in store.get_findings(baseline.id)}
    after = {f.id: f for f in store.get_findings(end.id)}
    return FindingsDelta(
        baseline_snapshot_id=baseline.id,
        baseline_at=baseline.created_at,
        end_snapshot_id=end.id,
        end_at=end.created_at,
        appeared=[f for fid, f in after.items() if fid not in before],
        cleared=[f for fid, f in before.items() if fid not in after],
    )


@router.get("/changes", response_model=EnvironmentChanges)
def environment_changes(
    since: str | None = Query(default=None, description="ISO 8601; default: last scan cycle"),
    until: str | None = Query(default=None, description="ISO 8601; default: now"),
    min_significance: str | None = None,
    limit_per_connection: int = Query(default=DEFAULT_PER_CONNECTION, ge=1, le=MAX_PER_CONNECTION),
) -> EnvironmentChanges:
    """Estate-wide change roll-up for [since, until] across every connection.

    Connections with nothing observed inside the window are listed with
    has_data=false rather than dropped. min_significance defaults to the
    changes_min_significance setting."""
    floor = _resolve_min_significance(min_significance)
    until_dt = _utc(_parse_time(until, "until") or store.now())
    parsed_since = _parse_time(since, "since")
    if parsed_since is not None:
        since_dt, window = _utc(parsed_since), "custom"
    else:
        derived = last_cycle_since(until_dt)
        since_dt, window = (derived or until_dt - timedelta(hours=24)), "last_cycle"
    if until_dt < since_dt:
        raise HTTPException(400, "until must not be earlier than since")

    sections: list[ConnectionWindow] = []
    totals = SignificanceCounts()
    appeared = cleared = covered = 0
    for conn in sorted(store.list_connections(), key=lambda c: c.name.lower()):
        rows = store.list_change_log(
            conn.id,
            since=since_dt,
            until=until_dt,
            min_significance=floor,
            limit=limit_per_connection + 1,
        )
        truncated = len(rows) > limit_per_connection
        if truncated:
            rows = rows[:limit_per_connection]
        # Counts come from the full window even when the list is capped.
        counted = store.count_changes_by_significance(conn.id, since=since_dt, until=until_dt)
        counts = SignificanceCounts(
            **{level: counted[level] for level in _at_or_above(floor)},
            total=sum(counted[level] for level in _at_or_above(floor)),
        )
        n_snaps = store.count_snapshots(conn.id, since=since_dt, until=until_dt)
        has_data = n_snaps > 0 or sum(counted.values()) > 0
        delta = _findings_delta(conn.id, since_dt, until_dt) if has_data else None
        if has_data:
            covered += 1
        for level in ("high", "medium", "low"):
            setattr(totals, level, getattr(totals, level) + getattr(counts, level))
        totals.total += counts.total
        if delta is not None:
            appeared += len(delta.appeared)
            cleared += len(delta.cleared)
        sections.append(
            ConnectionWindow(
                connection_id=conn.id,
                name=conn.name,
                host=conn.host,
                kind=conn.kind,
                has_data=has_data,
                snapshots_in_window=n_snaps,
                counts=counts,
                changes=rows,
                truncated=truncated,
                findings=delta,
            )
        )
    return EnvironmentChanges(
        since=since_dt,
        until=until_dt,
        window=window,
        min_significance=floor,
        totals=EnvironmentTotals(
            connections=len(sections),
            covered=covered,
            no_data=len(sections) - covered,
            changes=totals,
            findings_appeared=appeared,
            findings_cleared=cleared,
        ),
        connections=sections,
    )
