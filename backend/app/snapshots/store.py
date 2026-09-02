"""Persistence for connections, schedules, scan runs, snapshots, findings,
the retention policy and the persisted change log.

Every row is keyed by connection_id. Timestamps are ISO 8601 UTC strings in
SQLite and timezone-aware datetimes in Python. Snapshot resource lists are
stored gzip-compressed in snapshots.resources_gz; the legacy text column is
read as a fallback and emptied by migrate_legacy_snapshots().
"""

import gzip
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app import db
from app.config import settings as cfg
from app.events import store as events_store
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
from app.models.change import Change, ChangeRecord
from app.models.snapshot import RetentionPolicy, Tier

log = logging.getLogger("vcf_doctor.store")

RETENTION_POLICY_KEY = "retention_policy"
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SIG_ORDER = ("high", "medium", "low")
_SQL_CHUNK = 500  # ids per IN (...) clause; well under SQLite's variable limit


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
        c.execute("DELETE FROM changes WHERE connection_id = ?", (connection_id,))
        c.execute("DELETE FROM scan_runs WHERE connection_id = ?", (connection_id,))
        c.execute("DELETE FROM schedules WHERE connection_id = ?", (connection_id,))
        cur = c.execute("DELETE FROM connections WHERE id = ?", (connection_id,))
        removed = cur.rowcount > 0
    # Events live in their own lazily created table; drop them alongside the
    # change log so a deleted connection leaves nothing behind.
    events_store.delete_events(connection_id)
    return removed


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


def reconcile_interrupted_runs() -> int:
    """Mark runs left in 'running' by a crash or restart as errors.
    Called once at startup; returns how many were reconciled."""
    with db.transaction() as c:
        cur = c.execute(
            "UPDATE scan_runs SET finished = ?, status = 'error', "
            "error = 'interrupted by restart' WHERE status = 'running'",
            (now().isoformat(),),
        )
        return cur.rowcount


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


# --- retention policy ----------------------------------------------------


def default_retention_policy() -> RetentionPolicy:
    return RetentionPolicy(
        recent_days=cfg.retention_recent_days,
        hourly_days=cfg.retention_hourly_days,
        daily_days=cfg.retention_daily_days,
    )


def retention_policy() -> RetentionPolicy:
    """Stored policy, or the deployment defaults. The pre-tier `retention`
    count setting is deliberately not consulted."""
    raw = db.get_setting(RETENTION_POLICY_KEY)
    if raw:
        try:
            return RetentionPolicy.model_validate(raw)
        except ValidationError:
            log.warning("stored retention_policy is invalid, using defaults: %r", raw)
    return default_retention_policy()


def set_retention_policy(policy: RetentionPolicy) -> RetentionPolicy:
    db.set_setting(RETENTION_POLICY_KEY, policy.model_dump())
    return policy


def tier_for(created_at: datetime, scheduled: bool, policy: RetentionPolicy, at: datetime) -> Tier:
    if not scheduled:
        return "manual"
    age = at - created_at
    if age < timedelta(days=policy.recent_days):
        return "recent"
    if age < timedelta(days=policy.hourly_days):
        return "hourly"
    return "daily"


# --- snapshots -----------------------------------------------------------


def _row_to_summary(
    row, policy: RetentionPolicy | None = None, at: datetime | None = None
) -> SnapshotSummary:
    created = _dt(row["created_at"])
    return SnapshotSummary(
        id=row["id"],
        created_at=created,
        label=row["label"],
        connection_id=row["connection_id"],
        scheduled=bool(row["scheduled"]),
        resource_count=row["resource_count"],
        tier=tier_for(created, bool(row["scheduled"]), policy or retention_policy(), at or now()),
    )


_SUMMARY_COLS = "id, connection_id, created_at, label, scheduled, resource_count"


def _encode_resources(resources: list[Resource]) -> bytes:
    payload = json.dumps([r.model_dump(mode="json") for r in resources])
    return gzip.compress(payload.encode("utf-8"), compresslevel=6)


def _decode_resources(row) -> list[Resource]:
    blob = row["resources_gz"]
    if blob is not None:
        raw = gzip.decompress(blob)
    else:
        raw = row["resources"]
        if not raw:
            return []
    return [Resource.model_validate(r) for r in json.loads(raw)]


def _legacy_text_placeholder() -> str | None:
    """Databases created before the gzip change declared `resources` NOT NULL,
    so '' stands in for NULL there; new databases store NULL."""
    return None if db.column_is_nullable("snapshots", "resources") else ""


def save_snapshot(
    connection_id: str, resources: list[Resource], label: str, scheduled: bool
) -> Snapshot:
    sid = new_id()
    created = now()
    with db.transaction() as c:
        c.execute(
            "INSERT INTO snapshots(id, connection_id, created_at, label, scheduled, "
            "resource_count, resources, resources_gz) VALUES(?,?,?,?,?,?,?,?)",
            (
                sid,
                connection_id,
                created.isoformat(),
                label,
                int(scheduled),
                len(resources),
                _legacy_text_placeholder(),
                _encode_resources(resources),
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
        tier="recent" if scheduled else "manual",
    )


def migrate_legacy_snapshots(batch_size: int = 200) -> int:
    """Compress rows still holding JSON text in `resources`. Runs in batches,
    each under the write lock, so API reads interleave. Idempotent."""
    placeholder = _legacy_text_placeholder()
    total = 0
    while True:
        with db.transaction() as c:
            rows = c.execute(
                "SELECT id, resources FROM snapshots WHERE resources_gz IS NULL "
                "AND resources IS NOT NULL AND resources != '' LIMIT ?",
                (batch_size,),
            ).fetchall()
            if not rows:
                break
            c.executemany(
                "UPDATE snapshots SET resources_gz = ?, resources = ? WHERE id = ?",
                [
                    (
                        gzip.compress(r["resources"].encode("utf-8"), compresslevel=6),
                        placeholder,
                        r["id"],
                    )
                    for r in rows
                ],
            )
        total += len(rows)
        log.info("compressed %d legacy snapshot rows (%d so far)", len(rows), total)
    if total:
        log.info("legacy snapshot migration complete: %d rows compressed", total)
    return total


def list_snapshots(connection_id: str | None = None) -> list[SnapshotSummary]:
    q = f"SELECT {_SUMMARY_COLS} FROM snapshots"
    args: tuple = ()
    if connection_id:
        q += " WHERE connection_id = ?"
        args = (connection_id,)
    q += " ORDER BY created_at DESC"
    policy, at = retention_policy(), now()
    return [_row_to_summary(r, policy, at) for r in db.fetchall(q, args)]


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
    return Snapshot(**summary.model_dump(), resources=_decode_resources(row))


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


def delete_snapshots(snapshot_ids: list[str]) -> int:
    """Delete snapshots and their cached findings. Change rows are kept on
    purpose: the log outlives the snapshots it was computed from."""
    deleted = 0
    for i in range(0, len(snapshot_ids), _SQL_CHUNK):
        chunk = snapshot_ids[i : i + _SQL_CHUNK]
        marks = ",".join("?" * len(chunk))
        with db.transaction() as c:
            c.execute(f"DELETE FROM findings WHERE snapshot_id IN ({marks})", chunk)
            deleted += c.execute(f"DELETE FROM snapshots WHERE id IN ({marks})", chunk).rowcount
    return deleted


def _nearest_mark(t: datetime, period: timedelta) -> datetime:
    """The hour or day (00:00 UTC) mark closest to t; a half-way tie rounds up."""
    whole, rem = divmod(t - _EPOCH, period)
    mark = _EPOCH + whole * period
    return mark + period if rem * 2 >= period else mark


def select_retention_victims(
    rows: list[tuple[str, datetime]], policy: RetentionPolicy, at: datetime
) -> list[str]:
    """Pure tier selection over (id, created_at) pairs of scheduled snapshots.

    age < recent_days: keep all. recent <= age < hourly: group by nearest hour
    mark, keep the snapshot closest to the mark (ties: oldest, then id).
    hourly <= age < daily: same with day marks (00:00 UTC). age >= daily: prune.
    """
    recent = timedelta(days=policy.recent_days)
    hourly = timedelta(days=policy.hourly_days)
    daily = timedelta(days=policy.daily_days)
    best: dict[tuple[timedelta, datetime], tuple[timedelta, datetime, str]] = {}
    victims: list[str] = []
    for sid, created in rows:
        age = at - created
        if age < recent:
            continue
        if age >= daily:
            victims.append(sid)
            continue
        period = HOUR if age < hourly else DAY
        mark = _nearest_mark(created, period)
        candidate = (abs(created - mark), created, sid)
        current = best.get((period, mark))
        if current is None:
            best[(period, mark)] = candidate
        elif candidate < current:
            victims.append(current[2])
            best[(period, mark)] = candidate
        else:
            victims.append(sid)
    return victims


def apply_retention(
    connection_id: str, policy: RetentionPolicy | None = None, at: datetime | None = None
) -> int:
    """Prune scheduled snapshots per the tier policy and expire change rows
    older than daily_days. Manual snapshots are never touched. Returns the
    number of snapshots deleted."""
    policy = policy or retention_policy()
    at = at or now()
    rows = db.fetchall(
        "SELECT id, created_at FROM snapshots WHERE connection_id = ? AND scheduled = 1 "
        "ORDER BY created_at",
        (connection_id,),
    )
    victims = select_retention_victims([(r["id"], _dt(r["created_at"])) for r in rows], policy, at)
    deleted = delete_snapshots(victims) if victims else 0
    expired = prune_changes(connection_id, before=at - timedelta(days=policy.daily_days))
    if deleted or expired:
        log.info(
            "retention for %s: pruned %d snapshot(s), expired %d change row(s)",
            connection_id,
            deleted,
            expired,
        )
    return deleted


# --- change log -----------------------------------------------------------


def _row_to_change(row) -> ChangeRecord:
    return ChangeRecord(
        id=row["id"],
        connection_id=row["connection_id"],
        from_snapshot_id=row["from_snapshot_id"],
        to_snapshot_id=row["to_snapshot_id"],
        observed_at=_dt(row["observed_at"]),
        resource_id=row["resource_id"],
        resource_type=row["resource_type"],
        resource_name=row["resource_name"],
        change_type=row["change_type"],
        significance=row["significance"],
        summary=row["summary"],
        property_changes=json.loads(row["property_changes"] or "{}"),
    )


def save_changes(
    connection_id: str,
    from_snapshot_id: str,
    to_snapshot_id: str,
    observed_at: datetime,
    changes: list[Change],
) -> int:
    """Persist one scan's diff(previous, current). Every significance is
    stored; readers filter."""
    if not changes:
        return 0
    with db.transaction() as c:
        c.executemany(
            "INSERT INTO changes(id, connection_id, from_snapshot_id, to_snapshot_id, "
            "observed_at, resource_id, resource_type, resource_name, change_type, "
            "significance, summary, property_changes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    new_id(),
                    connection_id,
                    from_snapshot_id,
                    to_snapshot_id,
                    observed_at.isoformat(),
                    ch.resource_id,
                    ch.resource_type,
                    ch.resource_name,
                    ch.change_type,
                    ch.significance,
                    ch.summary,
                    json.dumps(
                        {k: v.model_dump(mode="json") for k, v in ch.property_changes.items()}
                    ),
                )
                for ch in changes
            ],
        )
    return len(changes)


def list_change_log(
    connection_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    min_significance: str = "low",
    resource_id: str | None = None,
    limit: int = 500,
) -> list[ChangeRecord]:
    """Newest first; within one observation, high significance first."""
    where: list[str] = []
    args: list[object] = []
    if connection_id:
        where.append("connection_id = ?")
        args.append(connection_id)
    if since is not None:
        where.append("observed_at >= ?")
        args.append(since.isoformat())
    if until is not None:
        where.append("observed_at <= ?")
        args.append(until.isoformat())
    if resource_id:
        where.append("resource_id = ?")
        args.append(resource_id)
    allowed = _SIG_ORDER[: _SIG_ORDER.index(min_significance) + 1]
    where.append(f"significance IN ({','.join('?' * len(allowed))})")
    args.extend(allowed)
    sig_rank = "CASE significance WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"
    q = (
        "SELECT * FROM changes WHERE "
        + " AND ".join(where)
        + f" ORDER BY observed_at DESC, {sig_rank}, resource_type, resource_name LIMIT ?"
    )
    args.append(limit)
    return [_row_to_change(r) for r in db.fetchall(q, tuple(args))]


def count_changes(connection_id: str) -> int:
    row = db.fetchone("SELECT COUNT(*) AS n FROM changes WHERE connection_id = ?", (connection_id,))
    return int(row["n"])


def prune_changes(connection_id: str, before: datetime) -> int:
    with db.transaction() as c:
        cur = c.execute(
            "DELETE FROM changes WHERE connection_id = ? AND observed_at < ?",
            (connection_id, before.isoformat()),
        )
        return cur.rowcount


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
