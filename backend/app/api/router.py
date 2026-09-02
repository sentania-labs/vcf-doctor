"""All /api routes except /api/assistant (Agent E) and /api/health (main.py)."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ValidationError

from app import db, scheduler
from app.assistant import settings as assistant_settings
from app.collectors.registry import CollectorUnavailable, get_collector
from app.config import settings
from app.diagnostics.scoring import compute_health
from app.models import (
    AssistantSettings,
    Connection,
    ConnectionCreate,
    ConnectionPublic,
    ConnectionResult,
    Finding,
    Resource,
    ScanRun,
    Schedule,
    ScheduleUpdate,
    Snapshot,
    SnapshotSummary,
)
from app.models.change import ChangeRecord
from app.models.snapshot import RetentionPolicy
from app.snapshots import store

router = APIRouter(prefix="/api")

SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}
SIGNIFICANCE_RANK = {"high": 0, "medium": 1, "low": 2}


def _connection_or_404(connection_id: str) -> Connection:
    conn = store.get_connection(connection_id)
    if conn is None:
        raise HTTPException(404, f"connection {connection_id} not found")
    return conn


def _target_connections(connection_id: str | None) -> list[Connection]:
    if connection_id:
        return [_connection_or_404(connection_id)]
    return store.list_connections()


def _latest_resources(connection_id: str | None) -> list[Resource]:
    out: list[Resource] = []
    for conn in _target_connections(connection_id):
        snap = store.latest_snapshot(conn.id)
        if snap:
            out.extend(snap.resources)
    return out


def _health_inputs(
    connection_id: str | None,
) -> tuple[list[Resource], list[Finding], dict[str, int]]:
    """Latest resources, their cached findings, and the objects each check
    evaluated (summed across connections), from one snapshot read per
    connection. Checks that compare against the previous snapshot count the
    previous snapshot's objects, and zero when there is none."""
    from app.diagnostics.registry import coverage, get_checks

    resources: list[Resource] = []
    findings: list[Finding] = []
    # Every check starts at zero so a connection with no snapshot yet reports
    # all checks as not evaluated rather than silently passed.
    cov: dict[str, int] = {c.id: 0 for c in get_checks()}
    for conn in _target_connections(connection_id):
        snaps = store.latest_snapshots(conn.id, 2)
        if not snaps:
            continue
        resources.extend(snaps[0].resources)
        findings.extend(store.get_findings(snaps[0].id))
        previous = snaps[1].resources if len(snaps) > 1 else None
        for check_id, n in coverage(snaps[0].resources, previous).items():
            cov[check_id] = cov.get(check_id, 0) + n
    findings.sort(key=lambda f: SEVERITY_RANK.get(f.severity, 9))
    return resources, findings, cov


def _latest_findings(connection_id: str | None) -> list[Finding]:
    out: list[Finding] = []
    for conn in _target_connections(connection_id):
        snap = store.latest_snapshot(conn.id)
        if snap:
            out.extend(store.get_findings(snap.id))
    out.sort(key=lambda f: SEVERITY_RANK.get(f.severity, 9))
    return out


SIGNIFICANCE_LEVELS = ("low", "medium", "high")
CHANGES_MIN_SIGNIFICANCE_KEY = "changes_min_significance"


def changes_min_significance() -> str:
    """Stored floor for the changes list; anything unexpected falls back to low."""
    value = db.get_setting(CHANGES_MIN_SIGNIFICANCE_KEY, "low")
    return value if value in SIGNIFICANCE_LEVELS else "low"


def _resolve_min_significance(requested: str | None) -> str:
    if requested is None or requested == "":
        return changes_min_significance()
    if requested not in SIGNIFICANCE_LEVELS:
        raise HTTPException(400, "min_significance must be one of low, medium, high")
    return requested


def _at_least(changes: list, min_significance: str) -> list:
    floor = SIGNIFICANCE_RANK.get(min_significance, 9)
    return [
        c for c in changes if SIGNIFICANCE_RANK.get(getattr(c, "significance", "low"), 9) <= floor
    ]


def _changes(
    connection_id: str | None,
    from_id: str | None,
    to_id: str | None,
    min_significance: str | None = None,
) -> list:
    floor = _resolve_min_significance(min_significance)
    return _at_least(_all_changes(connection_id, from_id, to_id), floor)


def _recent_changes(connection_id: str | None, min_significance: str | None) -> list:
    """Overview feed: the persisted log (last 24 h, or the newest observation
    when the last scan is older than that) for each connection that has one;
    the on-demand diff of the latest pair for connections that do not
    (databases predating the log). Sorted high significance first, then newest."""
    floor = _resolve_min_significance(min_significance)
    since = store.now() - timedelta(hours=24)
    out: list = []
    for conn in _target_connections(connection_id):
        if store.count_changes(conn.id):
            out.extend(_logged_changes(conn.id, since, floor))
            continue
        pair = store.latest_snapshots(conn.id, 2)
        if len(pair) == 2:
            out.extend(
                _at_least(scheduler.compute_changes(pair[1].resources, pair[0].resources), floor)
            )
    out.sort(
        key=lambda c: (
            SIGNIFICANCE_RANK.get(getattr(c, "significance", "low"), 9),
            -(getattr(c, "observed_at", None) or since).timestamp(),
        )
    )
    return out


def _logged_changes(connection_id: str, since: datetime, floor: str) -> list:
    """Change rows observed since `since`; when there are none (the last scan
    that found anything is older than the window) the rows of the newest
    observation instead, so the Overview never empties out between scans."""
    rows = store.list_change_log(connection_id, since=since, min_significance=floor)
    if rows:
        return rows
    newest = store.list_change_log(connection_id, min_significance=floor, limit=1)
    if not newest:
        return []
    observed = newest[0].observed_at
    return store.list_change_log(
        connection_id, since=observed, until=observed, min_significance=floor
    )


def _all_changes(connection_id: str | None, from_id: str | None, to_id: str | None) -> list:
    if from_id or to_id:
        to_snap = store.get_snapshot(to_id) if to_id else None
        from_snap = store.get_snapshot(from_id) if from_id else None
        if to_id and to_snap is None:
            raise HTTPException(404, f"snapshot {to_id} not found")
        if from_id and from_snap is None:
            raise HTTPException(404, f"snapshot {from_id} not found")
        cid = connection_id or (to_snap or from_snap).connection_id
        for snap in (from_snap, to_snap):
            if snap is not None and snap.connection_id != cid:
                raise HTTPException(400, "snapshots must belong to the same connection")
        if to_snap is None:
            to_snap = store.latest_snapshot(cid)
        if from_snap is None:
            older = [
                s
                for s in store.list_snapshots(cid)
                if to_snap and s.created_at < to_snap.created_at
            ]
            from_snap = store.get_snapshot(older[0].id) if older else None
        if from_snap is None or to_snap is None:
            return []
        return scheduler.compute_changes(from_snap.resources, to_snap.resources)
    out: list = []
    for conn in _target_connections(connection_id):
        pair = store.latest_snapshots(conn.id, 2)
        if len(pair) == 2:
            out.extend(scheduler.compute_changes(pair[1].resources, pair[0].resources))
    out.sort(key=lambda c: SIGNIFICANCE_RANK.get(getattr(c, "significance", "low"), 9))
    return out


# --- inventory -----------------------------------------------------------


@router.get("/resources", response_model=list[Resource])
def get_resources(connection_id: str | None = None, snapshot_id: str | None = None):
    if snapshot_id:
        snap = store.get_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(404, f"snapshot {snapshot_id} not found")
        return snap.resources
    return _latest_resources(connection_id)


@router.get("/findings", response_model=list[Finding])
def get_findings(connection_id: str | None = None, severity: str | None = None):
    findings = _latest_findings(connection_id)
    if severity:
        findings = [f for f in findings if f.severity == severity]
    return findings


# --- snapshots -----------------------------------------------------------


class SnapshotCreate(BaseModel):
    connection_id: str | None = None
    label: str | None = None


@router.get("/snapshots", response_model=list[SnapshotSummary])
def list_snapshots(connection_id: str | None = None):
    return store.list_snapshots(connection_id)


@router.get("/snapshots/{snapshot_id}", response_model=Snapshot)
def get_snapshot(snapshot_id: str):
    snap = store.get_snapshot(snapshot_id)
    if snap is None:
        raise HTTPException(404, f"snapshot {snapshot_id} not found")
    return snap


@router.post("/snapshots", response_model=Snapshot, status_code=201)
def create_snapshot(body: SnapshotCreate | None = None):
    body = body or SnapshotCreate()
    cid = body.connection_id
    if cid is None:
        conns = store.list_connections()
        if len(conns) != 1:
            raise HTTPException(400, "connection_id is required")
        cid = conns[0].id
    _connection_or_404(cid)
    run = scheduler.run_scan(cid, "manual", label=body.label)
    if run.status == "skipped":
        raise HTTPException(409, run.error or "scan already running")
    if run.status != "ok" or run.snapshot_id is None:
        raise HTTPException(502, run.error or "scan failed")
    return store.get_snapshot(run.snapshot_id)


@router.delete("/snapshots/{snapshot_id}")
def delete_snapshot(snapshot_id: str):
    if not store.delete_snapshot(snapshot_id):
        raise HTTPException(404, f"snapshot {snapshot_id} not found")
    return {"deleted": snapshot_id}


@router.get("/changes")
def get_changes(
    connection_id: str | None = None,
    from_id: str | None = Query(default=None, alias="from"),
    to_id: str | None = Query(default=None, alias="to"),
    min_significance: str | None = None,
) -> list[Any]:
    """min_significance (low|medium|high) defaults to the changes_min_significance setting."""
    return [
        c.model_dump(mode="json") if hasattr(c, "model_dump") else c
        for c in _changes(connection_id, from_id, to_id, min_significance)
    ]


def _parse_time(value: str | None, name: str) -> datetime | None:
    if value is None or value == "":
        return None
    # An unencoded "+HH:MM" offset reaches us as " HH:MM"; put the plus back.
    text = re.sub(r" (\d{2}:\d{2})$", r"+\1", value.strip().replace("Z", "+00:00"))
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(400, f"{name} must be an ISO 8601 timestamp") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@router.get("/changes/log", response_model=list[ChangeRecord])
def get_change_log(
    connection_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    min_significance: str | None = None,
    resource_id: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
):
    """Persisted per-scan diffs, newest first. Defaults: last 24 h, limit 500,
    min_significance from the changes_min_significance setting."""
    if connection_id:
        _connection_or_404(connection_id)
    since_dt = _parse_time(since, "since") or (store.now() - timedelta(hours=24))
    until_dt = _parse_time(until, "until")
    if until_dt is not None and until_dt < since_dt:
        raise HTTPException(400, "until must not be earlier than since")
    return store.list_change_log(
        connection_id,
        since=since_dt,
        until=until_dt,
        min_significance=_resolve_min_significance(min_significance),
        resource_id=resource_id,
        limit=limit,
    )


# --- scans ---------------------------------------------------------------


class ScanRequest(BaseModel):
    connection_id: str | None = None


@router.post("/scan", response_model=list[ScanRun])
def post_scan(body: ScanRequest | None = None):
    body = body or ScanRequest()
    if body.connection_id:
        _connection_or_404(body.connection_id)
        return [scheduler.run_scan(body.connection_id, "manual")]
    return scheduler.run_all_scans("manual")


@router.get("/scans", response_model=list[ScanRun])
def list_scans(connection_id: str | None = None, limit: int = 100):
    return store.list_runs(connection_id, limit=limit)


# --- connections ---------------------------------------------------------


class ConnectionUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    username: str | None = None
    password: str | None = None  # empty or omitted keeps the stored password
    verify_tls: bool | None = None
    kind: str | None = None
    interval_minutes: int | None = None
    enabled: bool | None = None


@router.get("/connections", response_model=list[ConnectionPublic])
def list_connections():
    return [store.public(c) for c in store.list_connections()]


def _check_kind(kind: str | None) -> None:
    """"vcenter" is the only operator-facing kind. "fixture" (bundled test
    data) is accepted only when the VCF_DOCTOR_TEST_FIXTURES hook is on."""
    if kind is None or kind == "vcenter":
        return
    if kind == "fixture" and settings.test_fixtures:
        return
    raise HTTPException(400, f"unknown connection kind: {kind}")


@router.post("/connections", response_model=ConnectionPublic, status_code=201)
def create_connection(body: ConnectionCreate):
    _check_kind(body.kind)
    if body.interval_minutes < settings.min_interval_minutes:
        body.interval_minutes = settings.min_interval_minutes
    conn = store.create_connection(body)
    scheduler.reschedule(conn.id)
    return store.public(conn)


@router.get("/connections/{connection_id}", response_model=ConnectionPublic)
def get_connection(connection_id: str):
    return store.public(_connection_or_404(connection_id))


@router.put("/connections/{connection_id}", response_model=ConnectionPublic)
def update_connection(connection_id: str, body: ConnectionUpdate):
    _connection_or_404(connection_id)
    fields = body.model_dump(exclude_unset=True)
    _check_kind(fields.get("kind"))
    if fields.get("interval_minutes") is not None:
        fields["interval_minutes"] = max(fields["interval_minutes"], settings.min_interval_minutes)
    conn = store.update_connection(connection_id, fields)
    scheduler.reschedule(connection_id)
    return store.public(conn)


@router.delete("/connections/{connection_id}")
def delete_connection(connection_id: str):
    _connection_or_404(connection_id)
    scheduler.remove_job(connection_id)
    store.delete_connection(connection_id)
    return {"deleted": connection_id}


@router.post("/connections/{connection_id}/test", response_model=ConnectionResult)
def test_connection(connection_id: str):
    conn = _connection_or_404(connection_id)
    try:
        return get_collector(conn).test_connection()
    except CollectorUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        return ConnectionResult(ok=False, message=str(exc)[:500])


class ConnectionTestBody(ConnectionCreate):
    """Test credentials before saving. Same shape as create."""


@router.post("/connections/test", response_model=ConnectionResult)
def test_unsaved_connection(body: ConnectionTestBody):
    _check_kind(body.kind)
    conn = Connection(id="unsaved", created_at=store.now(), **body.model_dump())
    try:
        return get_collector(conn).test_connection()
    except CollectorUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        return ConnectionResult(ok=False, message=str(exc)[:500])


@router.get("/connections/{connection_id}/schedule", response_model=Schedule)
def get_schedule(connection_id: str):
    _connection_or_404(connection_id)
    return store.get_schedule(connection_id)


@router.put("/connections/{connection_id}/schedule", response_model=Schedule)
def put_schedule(connection_id: str, body: ScheduleUpdate):
    _connection_or_404(connection_id)
    minutes = body.interval_minutes
    if minutes is not None:
        minutes = max(minutes, settings.min_interval_minutes)
    store.update_schedule(connection_id, interval_minutes=minutes, enabled=body.enabled)
    scheduler.reschedule(connection_id)
    return store.get_schedule(connection_id)


# --- settings ------------------------------------------------------------


class AppSettings(BaseModel):
    retention_policy: RetentionPolicy
    min_interval_minutes: int
    scheduler_running: bool
    changes_min_significance: str
    assistant: AssistantSettings


class AppSettingsUpdate(BaseModel):
    # Partial: omitted tiers keep their stored value. Ints, each >= 1,
    # recent_days <= hourly_days <= daily_days.
    retention_policy: dict[str, Any] | None = None
    changes_min_significance: str | None = None
    # Partial assistant update; may carry "api_key", which is stored and never echoed.
    assistant: dict[str, Any] | None = None


@router.get("/settings", response_model=AppSettings)
def get_settings():
    return AppSettings(
        retention_policy=scheduler.retention_policy(),
        min_interval_minutes=settings.min_interval_minutes,
        scheduler_running=scheduler.running(),
        changes_min_significance=changes_min_significance(),
        assistant=assistant_settings.get_settings(),
    )


_TIER_KEYS = ("recent_days", "hourly_days", "daily_days")


def _merge_retention_policy(update: dict[str, Any]) -> RetentionPolicy:
    unknown = set(update) - set(_TIER_KEYS)
    if unknown:
        raise HTTPException(400, f"unknown retention_policy keys: {', '.join(sorted(unknown))}")
    merged = scheduler.retention_policy().model_dump()
    for key, value in update.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise HTTPException(400, f"retention_policy.{key} must be an integer number of days")
        merged[key] = value
    try:
        return RetentionPolicy.model_validate(merged)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(x) for x in first.get("loc", ()))
        where = f"retention_policy.{loc}" if loc else "retention_policy"
        raise HTTPException(400, f"{where}: {first.get('msg', 'invalid')}") from exc


@router.put("/settings", response_model=AppSettings)
def put_settings(body: AppSettingsUpdate):
    if body.retention_policy is not None:
        store.set_retention_policy(_merge_retention_policy(body.retention_policy))
    if body.changes_min_significance is not None:
        if body.changes_min_significance not in SIGNIFICANCE_LEVELS:
            raise HTTPException(400, "changes_min_significance must be one of low, medium, high")
        db.set_setting(CHANGES_MIN_SIGNIFICANCE_KEY, body.changes_min_significance)
    if body.assistant:
        assistant_settings.update_settings(body.assistant)
    return get_settings()


# --- overview ------------------------------------------------------------


@router.get("/overview")
def overview(
    connection_id: str | None = None, min_significance: str | None = None
) -> dict[str, Any]:
    resources, findings, cov = _health_inputs(connection_id)
    changes = _recent_changes(connection_id, min_significance)

    by_sev = {"critical": 0, "warning": 0, "info": 0}
    for f in findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    by_type: dict[str, int] = {}
    for r in resources:
        by_type[r.type] = by_type.get(r.type, 0) + 1

    hosts = [r for r in resources if r.type == "host"]
    vms = [r for r in resources if r.type == "vm"]
    stores = [r for r in resources if r.type == "datastore"]
    capacity = sum(float(r.properties.get("capacity") or 0) for r in stores)
    free = sum(float(r.properties.get("freeSpace") or 0) for r in stores)
    storage_free_pct = round(free / capacity * 100, 1) if capacity else None

    # Findings cached for a check that no longer exists (renamed or removed
    # in an upgrade) still list, but do not score: the three check counts
    # must add up to the number of checks.
    health = compute_health([f for f in findings if f.check_id in cov], cov)
    last_scan = store.latest_run(connection_id)
    by_sev["passed"] = health["passed"]
    return {
        "health_score": health["score"],
        "health": health,
        "counts": by_sev,
        "resource_counts": by_type,
        "resource_total": len(resources),
        "resources": {"total": len(resources), "by_type": by_type},
        "hosts_connected": sum(
            1 for h in hosts if h.properties.get("connectionState") == "connected"
        ),
        "hosts_total": len(hosts),
        "vms_on": sum(1 for v in vms if v.properties.get("powerState") == "poweredOn"),
        "vms_total": len(vms),
        "storage_free_pct": storage_free_pct,
        "last_scan": ((last_scan.finished or last_scan.started).isoformat() if last_scan else None),
        "last_run": last_scan.model_dump(mode="json") if last_scan else None,
        "top_findings": [f.model_dump(mode="json") for f in findings[:5]],
        "recent_changes": [
            c.model_dump(mode="json") if hasattr(c, "model_dump") else c for c in changes[:5]
        ],
        "connections": len(_target_connections(connection_id)),
    }
