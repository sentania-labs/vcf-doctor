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
   diffing the newest pair of snapshots that actually differ. A database
   upgraded to the change log mid-life has a finding older than the first
   logged row, and no query over the log can reach the change that caused it
   (issue #41): diff the two snapshots bracketing first observation instead,
   or the newest differing pair when retention has pruned one of those two.
   The window says which case it is.

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

WindowBasis = Literal[
    "first_observed",
    "latest_differing_pair",
    "pre_log_bracketing_pair",
    "pre_log_differing_pair",
    "no_snapshots",
]


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
    # Oldest row the change log holds for this connection, set only when the
    # finding is older than it: the log cannot contain the cause (issue #41).
    log_starts_at: datetime | None = None


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
    seen_id: str | None = None
    interval_start: datetime | None = None  # the snapshot before that one, else seen_at
    interval_start_id: str | None = None  # None when no older snapshot survives
    count: int = 0  # how many consecutive snapshots hold it
    capped: bool = False  # MAX_SCANS_BACK stopped the walk
    all_surviving: bool = False  # present in every snapshot retention has kept


def first_observed(connection_id: str, finding_id: str) -> FirstObserved:
    snaps = store.list_snapshots(connection_id)  # newest first
    holders = store.snapshot_ids_with_finding(connection_id, finding_id)
    out = FirstObserved()
    for i, summary in enumerate(snaps[:MAX_SCANS_BACK]):
        if summary.id not in holders:
            break
        out.seen_at = summary.created_at
        out.seen_id = summary.id
        if i + 1 < len(snaps):
            out.interval_start = snaps[i + 1].created_at
            out.interval_start_id = snaps[i + 1].id
        else:
            out.interval_start = summary.created_at
            out.interval_start_id = None
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


def _log_coverage_start(connection_id: str, oldest) -> datetime:
    """The oldest moment the change log can speak about.

    The first logged row describes the interval that ends at its own
    observed_at, so coverage starts at the snapshot it diffed from: the newest
    snapshot before that stamp. Anything older is only in the snapshots, which
    is the whole of a database upgraded to the change log mid-life (issue #41).
    When that snapshot has itself been pruned, coverage starts at the row.
    """
    previous = store.snapshot_summary_at(connection_id, before=oldest.observed_at)
    return previous.created_at if previous is not None else oldest.observed_at


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


def _bracketing_pair(first: FirstObserved) -> list | None:
    """Diff of the snapshot before first observation against the first one
    holding the finding: the interval in which the cause happened. None when
    retention has pruned either side (or none older survives)."""
    if first.interval_start_id is None or first.seen_id is None:
        return None
    older = store.get_snapshot(first.interval_start_id)
    newer = store.get_snapshot(first.seen_id)
    if older is None or newer is None:
        return None
    return scheduler.compute_changes(older.resources, newer.resources)


def related_changes(connection_id: str, finding: Finding, resources: list[Resource]):
    near = neighbourhood(finding, resources)
    first = first_observed(connection_id, finding.id)
    seen_at, count, walk_capped = first.seen_at, first.count, first.capped
    if seen_at is None or first.interval_start is None:
        window = RelatedWindow(basis="no_snapshots")
        return FindingRelated(
            finding_id=finding.id, connection_id=connection_id, resource_ids=near, window=window
        )
    oldest_logged = store.oldest_change(connection_id)
    if oldest_logged is not None:
        floor = store.now() - MAX_WINDOW
        # The introducing diff is stamped with the first snapshot that holds the finding, or
        # with a snapshot retention has since pruned; either way it is at or after the
        # snapshot before (interval_start). When every surviving snapshot holds the finding
        # the log (which outlives snapshots) may still hold the cause: read back to the cap.
        log_since = floor if first.all_surviving else max(first.interval_start, floor)
        rows = _logged(connection_id, log_since, near)
        if not first.all_surviving:
            # The store's since is inclusive; a row stamped at interval_start is the diff that
            # ended at the pre-finding snapshot, so it predates the interval in which the
            # finding appeared. Keep only rows observed after that boundary.
            rows = [r for r in rows if r.observed_at > first.interval_start]
        since = max(first.interval_start, floor)
        if first.all_surviving and rows:
            since = min(since, max(min(r.observed_at for r in rows), floor))
        # A log that starts after the finding did cannot hold the change that
        # caused it: the pre-log era is only in the snapshots (issue #41).
        coverage_start = _log_coverage_start(connection_id, oldest_logged)
        pre_log = first.interval_start < coverage_start
        changes = _select(rows, near)
        near_set = set(near)
        window = RelatedWindow(
            basis="first_observed",
            since=since,
            until=None,
            first_observed=seen_at,
            scans_present=count,
            capped=walk_capped or first.interval_start < floor,
            log_starts_at=coverage_start if pre_log else None,
        )
        if pre_log and not any(c.resource_id in near_set for c in changes):
            diff = _bracketing_pair(first)
            if diff is not None:
                basis, pair_since, pair_until = (
                    "pre_log_bracketing_pair",
                    first.interval_start,
                    seen_at,
                )
            else:
                basis = "pre_log_differing_pair"
                diff, pair_since, pair_until = _latest_differing_pair(connection_id)
            fallback = _select(diff, near)
            if fallback:
                changes = fallback
                window = RelatedWindow(
                    basis=basis,
                    since=pair_since,
                    until=pair_until,
                    first_observed=seen_at,
                    scans_present=count,
                    capped=walk_capped,
                    log_starts_at=coverage_start,
                )
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
