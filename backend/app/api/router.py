"""All /api routes except /api/assistant (Agent E) and /api/health (main.py)."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app import db, scheduler
from app.assistant import settings as assistant_settings
from app.collectors.registry import CollectorUnavailable, get_collector
from app.config import settings
from app.diagnostics.registry import list_checks
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


@router.post("/connections", response_model=ConnectionPublic, status_code=201)
def create_connection(body: ConnectionCreate):
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
    retention: int
    min_interval_minutes: int
    demo_mode: bool
    scheduler_running: bool
    changes_min_significance: str
    assistant: AssistantSettings


class AppSettingsUpdate(BaseModel):
    retention: int | None = None
    changes_min_significance: str | None = None
    # Partial assistant update; may carry "api_key", which is stored and never echoed.
    assistant: dict[str, Any] | None = None


@router.get("/settings", response_model=AppSettings)
def get_settings():
    return AppSettings(
        retention=scheduler.retention(),
        min_interval_minutes=settings.min_interval_minutes,
        demo_mode=settings.demo_mode,
        scheduler_running=scheduler.running(),
        changes_min_significance=changes_min_significance(),
        assistant=assistant_settings.get_settings(),
    )


@router.put("/settings", response_model=AppSettings)
def put_settings(body: AppSettingsUpdate):
    if body.retention is not None:
        if body.retention < 1:
            raise HTTPException(400, "retention must be at least 1")
        db.set_setting("retention", body.retention)
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
    resources = _latest_resources(connection_id)
    findings = _latest_findings(connection_id)
    changes = _changes(connection_id, None, None, min_significance)

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

    score = max(0, 100 - 15 * by_sev["critical"] - 5 * by_sev["warning"])
    last_scan = store.latest_run(connection_id)
    checks_with_findings = {f.check_id for f in findings}
    by_sev["passed"] = max(0, len(list_checks()) - len(checks_with_findings))
    return {
        "health_score": score,
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
