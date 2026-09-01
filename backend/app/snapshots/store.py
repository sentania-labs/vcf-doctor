"""Persistence for connections, schedules, scan runs, snapshots and findings.

Every row is keyed by connection_id. Timestamps are ISO 8601 UTC strings in
SQLite and timezone-aware datetimes in Python.
"""

import json
import uuid
from datetime import UTC, datetime

from app import db
from app.models import (
    Connection,
    ConnectionCreate,
    ConnectionPublic,
    Finding,
    Resource,
    ScanRun,
    Schedule,
    Snapshot,
    SnapshotSummary,
)


def now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# --- connections ---------------------------------------------------------


def _row_to_connection(row) -> Connection:
    return Connection(
        id=row["id"],
        name=row["name"],
        host=row["host"],
        username=row["username"],
        password=row["password"],
        verify_tls=bool(row["verify_tls"]),
        kind=row["kind"],
        created_at=_dt(row["created_at"]),
        interval_minutes=row["interval_minutes"] if row["interval_minutes"] is not None else 15,
        enabled=bool(row["enabled"]) if row["enabled"] is not None else True,
    )


_CONN_SELECT = (
    "SELECT c.*, s.interval_minutes, s.enabled FROM connections c "
    "LEFT JOIN schedules s ON s.connection_id = c.id"
)


def public(conn: Connection) -> ConnectionPublic:
    return ConnectionPublic(
        id=conn.id,
        name=conn.name,
        host=conn.host,
        username=conn.username,
        verify_tls=conn.verify_tls,
        created_at=conn.created_at,
        kind=conn.kind,
    )


def list_connections() -> list[Connection]:
    rows = db.fetchall(_CONN_SELECT + " ORDER BY c.created_at")
    return [_row_to_connection(r) for r in rows]


def get_connection(connection_id: str) -> Connection | None:
    row = db.fetchone(_CONN_SELECT + " WHERE c.id = ?", (connection_id,))
    return _row_to_connection(row) if row else None


def create_connection(data: ConnectionCreate) -> Connection:
    cid = new_id()
    created = now()
    with db.transaction() as c:
        c.execute(
            "INSERT INTO connections(id, name, host, username, password, verify_tls, kind, "
            "created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                cid,
                data.name,
                data.host,
                data.username,
                data.password,
                int(data.verify_tls),
                data.kind,
                created.isoformat(),
            ),
        )
        c.execute(
            "INSERT INTO schedules(connection_id, interval_minutes, enabled) VALUES(?,?,?)",
            (cid, data.interval_minutes, int(data.enabled)),
        )
    return get_connection(cid)


def update_connection(connection_id: str, fields: dict) -> Connection | None:
    """Partial update. An empty password means "keep the stored one"."""
    existing = get_connection(connection_id)
    if existing is None:
        return None
    allowed = {"name", "host", "username", "password", "verify_tls", "kind"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if updates.get("password") == "":
        updates.pop("password")
    if "verify_tls" in updates:
        updates["verify_tls"] = int(updates["verify_tls"])
    with db.transaction() as c:
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            c.execute(
                f"UPDATE connections SET {sets} WHERE id = ?",
                (*updates.values(), connection_id),
            )
        sched = {k: fields[k] for k in ("interval_minutes", "enabled") if fields.get(k) is not None}
        if sched:
            if "enabled" in sched:
                sched["enabled"] = int(sched["enabled"])
            sets = ", ".join(f"{k} = ?" for k in sched)
            c.execute(
                f"UPDATE schedules SET {sets} WHERE connection_id = ?",
                (*sched.values(), connection_id),
            )
    return get_connection(connection_id)


def delete_connection(connection_id: str) -> bool:
    with db.transaction() as c:
        c.execute(
            "DELETE FROM findings WHERE snapshot_id IN "
            "(SELECT id FROM snapshots WHERE connection_id = ?)",
            (connection_id,),
        )
        c.execute("DELETE FROM snapshots WHERE connection_id = ?", (connection_id,))
        c.execute("DELETE FROM scan_runs WHERE connection_id = ?", (connection_id,))
        c.execute("DELETE FROM schedules WHERE connection_id = ?", (connection_id,))
        cur = c.execute("DELETE FROM connections WHERE id = ?", (connection_id,))
        return cur.rowcount > 0


# --- schedules -----------------------------------------------------------


def get_schedule(connection_id: str) -> Schedule | None:
    row = db.fetchone("SELECT * FROM schedules WHERE connection_id = ?", (connection_id,))
    if row is None:
        return None
    return Schedule(
        connection_id=row["connection_id"],
        interval_minutes=row["interval_minutes"],
        enabled=bool(row["enabled"]),
        last_run=_dt(row["last_run"]),
        next_run=_dt(row["next_run"]),
        last_status=row["last_status"],
    )


def update_schedule(
    connection_id: str,
    *,
    interval_minutes: int | None = None,
    enabled: bool | None = None,
    last_run: datetime | None = None,
    next_run: datetime | None = None,
    last_status: str | None = None,
    clear_next_run: bool = False,
) -> Schedule | None:
    sets: dict[str, object] = {}
    if interval_minutes is not None:
        sets["interval_minutes"] = interval_minutes
    if enabled is not None:
        sets["enabled"] = int(enabled)
    if last_run is not None:
        sets["last_run"] = _iso(last_run)
    if next_run is not None:
        sets["next_run"] = _iso(next_run)
    if clear_next_run:
        sets["next_run"] = None
    if last_status is not None:
        sets["last_status"] = last_status
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        with db.transaction() as c:
            c.execute(
                f"UPDATE schedules SET {cols} WHERE connection_id = ?",
                (*sets.values(), connection_id),
            )
    return get_schedule(connection_id)


# --- scan runs -----------------------------------------------------------


def _row_to_run(row) -> ScanRun:
    return ScanRun(
        id=row["id"],
        connection_id=row["connection_id"],
        started=_dt(row["started"]),
        finished=_dt(row["finished"]),
        status=row["status"],
        error=row["error"],
        snapshot_id=row["snapshot_id"],
        trigger=row["trigger"],
    )


def create_run(connection_id: str, trigger: str, status: str = "running") -> ScanRun:
    rid = new_id()
    started = now()
    with db.transaction() as c:
        c.execute(
            "INSERT INTO scan_runs(id, connection_id, started, status, trigger) VALUES(?,?,?,?,?)",
            (rid, connection_id, started.isoformat(), status, trigger),
        )
    return get_run(rid)


def finish_run(
    run_id: str, status: str, error: str | None = None, snapshot_id: str | None = None
) -> ScanRun:
    with db.transaction() as c:
        c.execute(
            "UPDATE scan_runs SET finished = ?, status = ?, error = ?, snapshot_id = ? "
            "WHERE id = ?",
            (now().isoformat(), status, error, snapshot_id, run_id),
        )
    return get_run(run_id)


def get_run(run_id: str) -> ScanRun | None:
    row = db.fetchone("SELECT * FROM scan_runs WHERE id = ?", (run_id,))
    return _row_to_run(row) if row else None


def list_runs(connection_id: str | None = None, limit: int = 100) -> list[ScanRun]:
    if connection_id:
        rows = db.fetchall(
            "SELECT * FROM scan_runs WHERE connection_id = ? ORDER BY started DESC LIMIT ?",
            (connection_id, limit),
        )
    else:
        rows = db.fetchall("SELECT * FROM scan_runs ORDER BY started DESC LIMIT ?", (limit,))
    return [_row_to_run(r) for r in rows]


def latest_run(connection_id: str | None = None) -> ScanRun | None:
    runs = list_runs(connection_id, limit=1)
    return runs[0] if runs else None


# --- snapshots -----------------------------------------------------------


def _row_to_summary(row) -> SnapshotSummary:
    return SnapshotSummary(
        id=row["id"],
        created_at=_dt(row["created_at"]),
        label=row["label"],
        connection_id=row["connection_id"],
        scheduled=bool(row["scheduled"]),
        resource_count=row["resource_count"],
    )


_SUMMARY_COLS = "id, connection_id, created_at, label, scheduled, resource_count"


def save_snapshot(
    connection_id: str, resources: list[Resource], label: str, scheduled: bool
) -> Snapshot:
    sid = new_id()
    created = now()
    payload = json.dumps([r.model_dump(mode="json") for r in resources])
    with db.transaction() as c:
        c.execute(
            "INSERT INTO snapshots(id, connection_id, created_at, label, scheduled, "
            "resource_count, resources) VALUES(?,?,?,?,?,?,?)",
            (
                sid,
                connection_id,
                created.isoformat(),
                label,
                int(scheduled),
                len(resources),
                payload,
            ),
        )
    return Snapshot(
        id=sid,
        created_at=created,
        label=label,
        connection_id=connection_id,
        scheduled=scheduled,
        resource_count=len(resources),
        resources=resources,
    )


def list_snapshots(connection_id: str | None = None) -> list[SnapshotSummary]:
    q = f"SELECT {_SUMMARY_COLS} FROM snapshots"
    args: tuple = ()
    if connection_id:
        q += " WHERE connection_id = ?"
        args = (connection_id,)
    q += " ORDER BY created_at DESC"
    return [_row_to_summary(r) for r in db.fetchall(q, args)]


def count_snapshots(connection_id: str) -> int:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM snapshots WHERE connection_id = ?", (connection_id,)
    )
    return int(row["n"])


def get_snapshot(snapshot_id: str) -> Snapshot | None:
    row = db.fetchone("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,))
    if row is None:
        return None
    summary = _row_to_summary(row)
    resources = [Resource.model_validate(r) for r in json.loads(row["resources"])]
    return Snapshot(**summary.model_dump(), resources=resources)


def latest_snapshots(connection_id: str, n: int = 2) -> list[Snapshot]:
    """Newest first. Used for "latest" and "previous" lookups."""
    rows = db.fetchall(
        f"SELECT {_SUMMARY_COLS} FROM snapshots WHERE connection_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (connection_id, n),
    )
    return [get_snapshot(r["id"]) for r in rows]


def latest_snapshot(connection_id: str) -> Snapshot | None:
    found = latest_snapshots(connection_id, 1)
    return found[0] if found else None


def delete_snapshot(snapshot_id: str) -> bool:
    with db.transaction() as c:
        c.execute("DELETE FROM findings WHERE snapshot_id = ?", (snapshot_id,))
        cur = c.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
        return cur.rowcount > 0


def prune_scheduled(connection_id: str, keep: int) -> int:
    """Delete scheduled snapshots beyond the newest `keep`. Manual ones are never touched."""
    rows = db.fetchall(
        "SELECT id FROM snapshots WHERE connection_id = ? AND scheduled = 1 "
        "ORDER BY created_at DESC",
        (connection_id,),
    )
    victims = [r["id"] for r in rows[max(keep, 0) :]]
    if not victims:
        return 0
    marks = ",".join("?" * len(victims))
    with db.transaction() as c:
        c.execute(f"DELETE FROM findings WHERE snapshot_id IN ({marks})", victims)
        c.execute(f"DELETE FROM snapshots WHERE id IN ({marks})", victims)
    return len(victims)


# --- findings cache -------------------------------------------------------


def save_findings(snapshot_id: str, findings: list[Finding]) -> None:
    payload = json.dumps([f.model_dump(mode="json") for f in findings])
    with db.transaction() as c:
        c.execute(
            "INSERT INTO findings(snapshot_id, findings) VALUES(?, ?) "
            "ON CONFLICT(snapshot_id) DO UPDATE SET findings = excluded.findings",
            (snapshot_id, payload),
        )


def get_findings(snapshot_id: str) -> list[Finding]:
    row = db.fetchone("SELECT findings FROM findings WHERE snapshot_id = ?", (snapshot_id,))
    if row is None:
        return []
    return [Finding.model_validate(f) for f in json.loads(row["findings"])]
