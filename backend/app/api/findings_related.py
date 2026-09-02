"""GET /api/findings/{finding_id}/related: the changes around a finding.

The Health drawer used to diff only the two newest snapshots, so two scans
with nothing between them showed no related changes even when the cause was
one scan older (issue #5). This walks back instead:

1. Work out when the finding was first observed: the oldest consecutive
   snapshot (newest first) whose cached findings still contain this finding id.
2. Read the persisted change log for the connection since that time and keep
   the rows about the finding's object, its parent, its children and its
   related objects, plus any high-significance row on the connection.
3. Databases predating the change log have no rows at all; then fall back to
   diffing the newest pair of snapshots that actually differ.

The window is capped (MAX_WINDOW, MAX_SCANS_BACK) and the response says which
window it shows, so the drawer can print it.
"""

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import scheduler
from app.models import Finding, Resource
from app.models.change import Change
from app.snapshots import store

router = APIRouter(prefix="/api")

MAX_WINDOW = timedelta(days=30)  # never look further back than this
MAX_SCANS_BACK = 200  # snapshots inspected when locating first observation
MAX_PAIRS_BACK = 20  # snapshot pairs diffed in the no-log fallback
MAX_CHANGES = 12  # rows returned to the drawer
LOG_FETCH_LIMIT = 1000  # per query; rows are then sorted oldest first and capped
SIGNIFICANCE_RANK = {"high": 0, "medium": 1, "low": 2}

WindowBasis = Literal["first_observed", "latest_differing_pair", "no_snapshots"]


class RelatedWindow(BaseModel):
    """since starts at the snapshot before the finding first appeared: the
    change that introduced it (and the vCenter events around it) happened in
    that scan interval, and the diff row is stamped with the later snapshot."""

    basis: WindowBasis
    since: datetime | None = None
    until: datetime | None = None  # None means "now"
    first_observed: datetime | None = None  # created_at of the first snapshot holding the finding
    scans_present: int = 0  # consecutive snapshots (newest first) containing the finding
    capped: bool = False  # true when MAX_WINDOW or MAX_SCANS_BACK cut the walk short


class FindingRelated(BaseModel):
    finding_id: str
    connection_id: str
    resource_ids: list[str] = Field(default_factory=list)  # object and its neighbours
    window: RelatedWindow
    changes: list[Change] = Field(default_factory=list)


def _locate(finding_id: str, connection_id: str | None) -> tuple[str, Finding, list[Resource]]:
    """The finding as it stands in the latest snapshot of the connection that owns it."""
    if connection_id:
        conns = [store.get_connection(connection_id)]
        if conns[0] is None:
            raise HTTPException(404, f"connection {connection_id} not found")
    else:
        conns = store.list_connections()
    for conn in conns:
        snap = store.latest_snapshot(conn.id)
        if snap is None:
            continue
        for f in store.get_findings(snap.id):
            if f.id == finding_id:
                return conn.id, f, snap.resources
    raise HTTPException(404, f"finding {finding_id} not found in the latest snapshot")


def neighbourhood(finding: Finding, resources: list[Resource]) -> list[str]:
    """The finding's object plus parent, children and relationship targets."""
    if not finding.resource_id:
        return []
    near: list[str] = [finding.resource_id]
    target = next((r for r in resources if r.id == finding.resource_id), None)
    if target is None:
        return near
    if target.parent_id:
        near.append(target.parent_id)
    near.extend(rel.target_id for rel in target.relationships)
    near.extend(r.id for r in resources if r.parent_id == target.id)
    return list(dict.fromkeys(near))


class FirstObserved(BaseModel):
    seen_at: datetime | None = None  # oldest consecutive snapshot holding the finding
    interval_start: datetime | None = None  # the snapshot before that one, else seen_at
    count: int = 0  # how many consecutive snapshots hold it
    capped: bool = False  # MAX_SCANS_BACK stopped the walk
    all_surviving: bool = False  # present in every snapshot retention has kept


def first_observed(connection_id: str, finding_id: str) -> FirstObserved:
    snaps = store.list_snapshots(connection_id)  # newest first
    out = FirstObserved()
    for i, summary in enumerate(snaps[:MAX_SCANS_BACK]):
        if not any(f.id == finding_id for f in store.get_findings(summary.id)):
            break
        out.seen_at = summary.created_at
        out.interval_start = snaps[i + 1].created_at if i + 1 < len(snaps) else summary.created_at
        out.count = i + 1
    else:
        out.capped = len(snaps) > MAX_SCANS_BACK
        out.all_surviving = not out.capped and bool(snaps)
    return out


def _key(c) -> tuple:
    """Oldest first, so the change that introduced the finding leads and
    survives the MAX_CHANGES cap; high significance first within one scan."""
    observed = getattr(c, "observed_at", None)
    stamp = observed.timestamp() if observed else 0
    return (stamp, SIGNIFICANCE_RANK.get(c.significance, 9), c.resource_name)


def _select(changes: list, near: list[str]) -> list[Change]:
    """Rows about the neighbourhood (in the order of `near`: the object itself
    first), then other high-significance rows; oldest first, capped."""
    seen: set[str] = set()
    picked: list = []
    for rid in near:
        for c in sorted((c for c in changes if c.resource_id == rid), key=_key):
            marker = getattr(c, "id", None) or (rid, c.summary)
            if marker not in seen:
                seen.add(marker)
                picked.append(c)
    near_set = set(near)
    rest = [c for c in changes if c.resource_id not in near_set and c.significance == "high"]
    picked.extend(sorted(rest, key=_key))
    return [Change.model_validate(c.model_dump()) for c in picked[:MAX_CHANGES]]


def _logged(connection_id: str, since: datetime, near: list[str]) -> list:
    """Per-object queries (the store filters by one resource id) plus the
    connection's high rows, so a busy log cannot push the cause off the end."""
    rows: list = []
    for rid in near:
        rows.extend(
            store.list_change_log(
                connection_id, since=since, resource_id=rid, limit=LOG_FETCH_LIMIT
            )
        )
    rows.extend(
        store.list_change_log(
            connection_id, since=since, min_significance="high", limit=LOG_FETCH_LIMIT
        )
    )
    unique = {r.id: r for r in rows}
    return list(unique.values())


def _latest_differing_pair(connection_id: str) -> tuple[list, datetime | None, datetime | None]:
    """Fallback when nothing is logged: diff newest pairs until one differs."""
    summaries = store.list_snapshots(connection_id)[: MAX_PAIRS_BACK + 1]
    newer = store.get_snapshot(summaries[0].id) if summaries else None
    for summary in summaries[1:]:
        older = store.get_snapshot(summary.id)
        if newer is None or older is None:
            break
        diff = scheduler.compute_changes(older.resources, newer.resources)
        if diff:
            return diff, older.created_at, newer.created_at
        newer = older
    return [], None, None


def related_changes(connection_id: str, finding: Finding, resources: list[Resource]):
    near = neighbourhood(finding, resources)
    first = first_observed(connection_id, finding.id)
    seen_at, count, walk_capped = first.seen_at, first.count, first.capped
    if seen_at is None or first.interval_start is None:
        window = RelatedWindow(basis="no_snapshots")
        return FindingRelated(
            finding_id=finding.id, connection_id=connection_id, resource_ids=near, window=window
        )
    if store.count_changes(connection_id):
        floor = store.now() - MAX_WINDOW
        # The introducing diff is stamped with the first snapshot that holds the finding, or
        # with a snapshot retention has since pruned; either way it is at or after the
        # snapshot before (interval_start). When every surviving snapshot holds the finding
        # the log (which outlives snapshots) may still hold the cause: read back to the cap.
        log_since = floor if first.all_surviving else max(first.interval_start, floor)
        rows = _logged(connection_id, log_since, near)
        since = max(first.interval_start, floor)
        if first.all_surviving and rows:
            since = min(since, max(min(r.observed_at for r in rows), floor))
        window = RelatedWindow(
            basis="first_observed",
            since=since,
            until=None,
            first_observed=seen_at,
            scans_present=count,
            capped=walk_capped or first.interval_start < floor,
        )
        changes = _select(rows, near)
    else:
        diff, since, until = _latest_differing_pair(connection_id)
        window = RelatedWindow(
            basis="latest_differing_pair",
            since=since,
            until=until,
            first_observed=seen_at,
            scans_present=count,
            capped=walk_capped,
        )
        changes = _select(diff, near)
    return FindingRelated(
        finding_id=finding.id,
        connection_id=connection_id,
        resource_ids=near,
        window=window,
        changes=changes,
    )


@router.get("/findings/{finding_id}/related", response_model=FindingRelated)
def get_finding_related(finding_id: str, connection_id: str | None = None):
    """Changes around a finding since it was first observed (see module docstring)."""
    cid, finding, resources = _locate(finding_id, connection_id)
    return related_changes(cid, finding, resources)
