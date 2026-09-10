"""Persistence for connections, schedules, scan runs, snapshots, findings,
the retention policy and the persisted change log.

Every row is keyed by connection_id. Timestamps are ISO 8601 UTC strings in
PostgreSQL and timezone-aware datetimes in Python. Snapshot resource lists are
stored gzip-compressed in snapshots.resources_gz.
"""

import gzip
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo

from pydantic import ValidationError

from app import db, timezones, vault
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
_DELETE_CHUNK = 500  # ids deleted per transaction, so one prune is not one huge write


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
    try:
        password = vault.decrypt(row["password"])
        unreadable = False
    except vault.SecretUnreadable:
        # Key lost or rotated. Keep the row usable for everything except the
        # password; the operator re-enters it from the Connections page.
        # Fixture connections carry no real credential, so nothing to re-enter.
        password, unreadable = "", row["kind"] != "fixture"
    return Connection(
        id=row["id"],
        name=row["name"],
        host=row["host"],
        username=row["username"],
        password=password,
        credentials_unreadable=unreadable,
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
        needs_credentials=conn.credentials_unreadable,
    )


def list_connections() -> list[Connection]:
    rows = db.fetchall(_CONN_SELECT + " ORDER BY c.created_at")
    return [_row_to_connection(r) for r in rows]


def get_connection(connection_id: str) -> Connection | None:
    row = db.fetchone(_CONN_SELECT + " WHERE c.id = %s", (connection_id,))
    return _row_to_connection(row) if row else None


def create_connection(data: ConnectionCreate) -> Connection:
    cid = new_id()
    created = now()
    with db.transaction() as c:
        c.execute(
            "INSERT INTO connections(id, name, host, username, password, verify_tls, kind, "
            "created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                cid,
                data.name,
                data.host,
                data.username,
                vault.encrypt(data.password),
                bool(data.verify_tls),
                data.kind,
                created.isoformat(),
            ),
        )
        c.execute(
            "INSERT INTO schedules(connection_id, interval_minutes, enabled) "
            "VALUES(%s,%s,%s)",
            (cid, data.interval_minutes, bool(data.enabled)),
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
    if "password" in updates:
        updates["password"] = vault.encrypt(updates["password"])
    if "verify_tls" in updates:
        updates["verify_tls"] = bool(updates["verify_tls"])
    with db.transaction() as c:
        if updates:
            sets = ", ".join(f"{k} = %s" for k in updates)
            c.execute(
                f"UPDATE connections SET {sets} WHERE id = %s",
                (*updates.values(), connection_id),
            )
        sched = {k: fields[k] for k in ("interval_minutes", "enabled") if fields.get(k) is not None}
        if sched:
            if "enabled" in sched:
                sched["enabled"] = bool(sched["enabled"])
            sets = ", ".join(f"{k} = %s" for k in sched)
            c.execute(
                f"UPDATE schedules SET {sets} WHERE connection_id = %s",
                (*sched.values(), connection_id),
            )
    return get_connection(connection_id)


def delete_connection(connection_id: str) -> bool:
    with db.transaction() as c:
        c.execute(
            "DELETE FROM findings WHERE snapshot_id IN "
            "(SELECT id FROM snapshots WHERE connection_id = %s)",
            (connection_id,),
        )
        c.execute("DELETE FROM snapshots WHERE connection_id = %s", (connection_id,))
        c.execute("DELETE FROM changes WHERE connection_id = %s", (connection_id,))
        c.execute("DELETE FROM scan_runs WHERE connection_id = %s", (connection_id,))
        c.execute("DELETE FROM schedules WHERE connection_id = %s", (connection_id,))
        c.execute("DELETE FROM connections WHERE id = %s", (connection_id,))
        removed = c.rowcount > 0
    # Events are owned by app/events; drop them alongside the change log so a
    # deleted connection leaves nothing behind.
    events_store.delete_events(connection_id)
    return removed


# --- schedules -----------------------------------------------------------


def get_schedule(connection_id: str) -> Schedule | None:
    row = db.fetchone("SELECT * FROM schedules WHERE connection_id = %s", (connection_id,))
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
        sets["enabled"] = bool(enabled)
    if last_run is not None:
        sets["last_run"] = _iso(last_run)
    if next_run is not None:
        sets["next_run"] = _iso(next_run)
    if clear_next_run:
        sets["next_run"] = None
    if last_status is not None:
        sets["last_status"] = last_status
    if sets:
        cols = ", ".join(f"{k} = %s" for k in sets)
        with db.transaction() as c:
            c.execute(
                f"UPDATE schedules SET {cols} WHERE connection_id = %s",
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
            "INSERT INTO scan_runs(id, connection_id, started, status, trigger) "
            "VALUES(%s,%s,%s,%s,%s)",
            (rid, connection_id, started.isoformat(), status, trigger),
        )
    return get_run(rid)


def finish_run(
    run_id: str, status: str, error: str | None = None, snapshot_id: str | None = None
) -> ScanRun:
    with db.transaction() as c:
        c.execute(
            "UPDATE scan_runs SET finished = %s, status = %s, error = %s, snapshot_id = %s "
            "WHERE id = %s",
            (now().isoformat(), status, error, snapshot_id, run_id),
        )
    return get_run(run_id)


def reconcile_interrupted_runs() -> int:
    """Mark runs left in 'running' by a crash or restart as errors.

    A scan in flight holds its connection's scan lock; a row whose connection
    lock is free belongs to a process that is gone. Returns how many were
    reconciled.
    """
    reconciled = 0
    stuck = db.fetchall("SELECT DISTINCT connection_id FROM scan_runs WHERE status = 'running'")
    for row in stuck:
        connection_id = row["connection_id"]
        with db.try_advisory_lock(db.SCAN_LOCK, connection_id) as idle:
            if not idle:
                continue  # someone is scanning this connection right now
            with db.transaction() as c:
                c.execute(
                    "UPDATE scan_runs SET finished = %s, status = 'error', "
                    "error = 'interrupted by restart' "
                    "WHERE status = 'running' AND connection_id = %s",
                    (now().isoformat(), connection_id),
                )
                reconciled += c.rowcount
    return reconciled


def get_run(run_id: str) -> ScanRun | None:
    row = db.fetchone("SELECT * FROM scan_runs WHERE id = %s", (run_id,))
    return _row_to_run(row) if row else None


def list_runs(connection_id: str | None = None, limit: int = 100) -> list[ScanRun]:
    if connection_id:
        rows = db.fetchall(
            "SELECT * FROM scan_runs WHERE connection_id = %s ORDER BY started DESC LIMIT %s",
            (connection_id, limit),
        )
    else:
        rows = db.fetchall("SELECT * FROM scan_runs ORDER BY started DESC LIMIT %s", (limit,))
    return [_row_to_run(r) for r in rows]


def latest_run(connection_id: str | None = None) -> ScanRun | None:
    runs = list_runs(connection_id, limit=1)
    return runs[0] if runs else None


# --- retention policy ----------------------------------------------------


def default_retention_policy() -> RetentionPolicy:
    tz = cfg.retention_timezone
    if not timezones.is_valid(tz):
        log.warning("unknown retention timezone %r in the environment, using UTC", tz)
        tz = "UTC"
    return RetentionPolicy(
        recent_days=cfg.retention_recent_days,
        hourly_days=cfg.retention_hourly_days,
        daily_days=cfg.retention_daily_days,
        timezone=tz,
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
    resolved_policy = policy or retention_policy()
    return SnapshotSummary(
        id=row["id"],
        created_at=created,
        label=row["label"],
        connection_id=row["connection_id"],
        scheduled=bool(row["scheduled"]),
        resource_count=row["resource_count"],
        tier=tier_for(created, bool(row["scheduled"]), resolved_policy, at or now()),
        retention_day=(
            created.astimezone(timezones.zone(resolved_policy.timezone)).date().isoformat()
        ),
    )


_SUMMARY_COLS = "id, connection_id, created_at, label, scheduled, resource_count"


def _encode_resources(resources: list[Resource]) -> bytes:
    payload = json.dumps([r.model_dump(mode="json") for r in resources])
    return gzip.compress(payload.encode("utf-8"), compresslevel=6)


def _decode_resources(row) -> list[Resource]:
    blob = row["resources_gz"]
    if not blob:
        return []
    return [Resource.model_validate(r) for r in json.loads(gzip.decompress(bytes(blob)))]


def save_snapshot(
    connection_id: str, resources: list[Resource], label: str, scheduled: bool
) -> Snapshot:
    sid = new_id()
    created = now()
    policy = retention_policy()
    with db.transaction() as c:
        c.execute(
            "INSERT INTO snapshots(id, connection_id, created_at, label, scheduled, "
            "resource_count, resources_gz) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (
                sid,
                connection_id,
                created.isoformat(),
                label,
                bool(scheduled),
                len(resources),
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
        retention_day=created.astimezone(timezones.zone(policy.timezone)).date().isoformat(),
    )


def list_snapshots(connection_id: str | None = None) -> list[SnapshotSummary]:
    q = f"SELECT {_SUMMARY_COLS} FROM snapshots"
    args: tuple = ()
    if connection_id:
        q += " WHERE connection_id = %s"
        args = (connection_id,)
    q += " ORDER BY created_at DESC"
    policy, at = retention_policy(), now()
    return [_row_to_summary(r, policy, at) for r in db.fetchall(q, args)]


def count_snapshots(
    connection_id: str, since: datetime | None = None, until: datetime | None = None
) -> int:
    """Snapshots for a connection, optionally only those created inside [since, until]."""
    q = "SELECT COUNT(*) AS n FROM snapshots WHERE connection_id = %s"
    args: list[object] = [connection_id]
    if since is not None:
        q += " AND created_at >= %s"
        args.append(since.isoformat())
    if until is not None:
        q += " AND created_at <= %s"
        args.append(until.isoformat())
    return int(db.fetchone(q, tuple(args))["n"])


def snapshot_summary_at(
    connection_id: str, *, before: datetime | None = None, at_or_before: datetime | None = None
) -> SnapshotSummary | None:
    """The newest snapshot created strictly before `before`, or at or before
    `at_or_before` (one of the two). Used to find window boundaries."""
    q = f"SELECT {_SUMMARY_COLS} FROM snapshots WHERE connection_id = %s"
    args: list[object] = [connection_id]
    if before is not None:
        q += " AND created_at < %s"
        args.append(before.isoformat())
    if at_or_before is not None:
        q += " AND created_at <= %s"
        args.append(at_or_before.isoformat())
    row = db.fetchone(q + " ORDER BY created_at DESC LIMIT 1", tuple(args))
    return _row_to_summary(row) if row is not None else None


def earliest_snapshot_summary_since(
    connection_id: str, since: datetime, until: datetime | None = None
) -> SnapshotSummary | None:
    """The oldest snapshot created at or after `since` (and at or before `until`)."""
    q = f"SELECT {_SUMMARY_COLS} FROM snapshots WHERE connection_id = %s AND created_at >= %s"
    args: list[object] = [connection_id, since.isoformat()]
    if until is not None:
        q += " AND created_at <= %s"
        args.append(until.isoformat())
    row = db.fetchone(q + " ORDER BY created_at ASC LIMIT 1", tuple(args))
    return _row_to_summary(row) if row is not None else None


def existing_snapshot_ids(snapshot_ids: list[str]) -> set[str]:
    """Which of the given ids still have a snapshot row (change rows outlive pruning)."""
    ids = sorted(set(snapshot_ids))
    if not ids:
        return set()
    rows = db.fetchall("SELECT id FROM snapshots WHERE id = ANY(%s)", (ids,))
    return {r["id"] for r in rows}


def snapshot_summary(snapshot_id: str) -> SnapshotSummary | None:
    """One snapshot's summary without decoding its resources."""
    row = db.fetchone(f"SELECT {_SUMMARY_COLS} FROM snapshots WHERE id = %s", (snapshot_id,))
    return _row_to_summary(row) if row is not None else None


def get_snapshot(snapshot_id: str) -> Snapshot | None:
    row = db.fetchone("SELECT * FROM snapshots WHERE id = %s", (snapshot_id,))
    if row is None:
        return None
    summary = _row_to_summary(row)
    return Snapshot(**summary.model_dump(), resources=_decode_resources(row))


def latest_snapshots(connection_id: str, n: int = 2) -> list[Snapshot]:
    """Newest first. Used for "latest" and "previous" lookups."""
    rows = db.fetchall(
        f"SELECT {_SUMMARY_COLS} FROM snapshots WHERE connection_id = %s "
        "ORDER BY created_at DESC LIMIT %s",
        (connection_id, n),
    )
    return [get_snapshot(r["id"]) for r in rows]


def latest_snapshot(connection_id: str) -> Snapshot | None:
    found = latest_snapshots(connection_id, 1)
    return found[0] if found else None


def delete_snapshot(snapshot_id: str) -> bool:
    with db.transaction() as c:
        c.execute("DELETE FROM findings WHERE snapshot_id = %s", (snapshot_id,))
        c.execute("DELETE FROM snapshots WHERE id = %s", (snapshot_id,))
        return c.rowcount > 0


def delete_snapshots(snapshot_ids: list[str]) -> int:
    """Delete snapshots and their cached findings. Change rows are kept on
    purpose: the log outlives the snapshots it was computed from."""
    deleted = 0
    for i in range(0, len(snapshot_ids), _DELETE_CHUNK):
        chunk = snapshot_ids[i : i + _DELETE_CHUNK]
        with db.transaction() as c:
            c.execute("DELETE FROM findings WHERE snapshot_id = ANY(%s)", (chunk,))
            c.execute("DELETE FROM snapshots WHERE id = ANY(%s)", (chunk,))
            deleted += c.rowcount
    return deleted


def _nearest_mark(t: datetime, period: timedelta) -> datetime:
    """The hour mark closest to t; a half-way tie rounds up."""
    whole, rem = divmod(t - _EPOCH, period)
    mark = _EPOCH + whole * period
    return mark + period if rem * 2 >= period else mark


def _day_mark(t: datetime, tz: tzinfo) -> datetime:
    """The starting midnight of t's local calendar day."""
    local = t.astimezone(tz)
    return datetime.combine(local.date(), time.min, tzinfo=tz)


def select_retention_victims(
    rows: list[tuple[str, datetime]], policy: RetentionPolicy, at: datetime
) -> list[str]:
    """Pure tier selection over (id, created_at) pairs of scheduled snapshots.

    age < recent_days: keep all. recent <= age < hourly: group by nearest hour
    mark, keep the snapshot closest to the mark (ties: oldest, then id).
    hourly <= age < daily: group by local calendar day and keep the snapshot
    nearest that day's starting midnight. age >= daily: prune.
    """
    recent = timedelta(days=policy.recent_days)
    hourly = timedelta(days=policy.hourly_days)
    daily = timedelta(days=policy.daily_days)
    tz = timezones.zone(policy.timezone)
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
        mark = _nearest_mark(created, HOUR) if period is HOUR else _day_mark(created, tz)
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
    """Apply snapshot, change-log, and independent event limits."""
    policy = policy or retention_policy()
    at = at or now()
    rows = db.fetchall(
        "SELECT id, created_at FROM snapshots WHERE connection_id = %s AND scheduled "
        "ORDER BY created_at",
        (connection_id,),
    )
    victims = select_retention_victims([(r["id"], _dt(r["created_at"])) for r in rows], policy, at)
    deleted = delete_snapshots(victims) if victims else 0
    expired = prune_changes(connection_id, before=at - timedelta(days=policy.daily_days))
    event_policy = events_store.event_policy()
    events_gone = events_store.prune_events(connection_id, event_policy.retention_hours, now=at)
    over_cap = events_store.enforce_row_cap(connection_id, event_policy.row_cap)
    if deleted or expired or events_gone or over_cap:
        log.info(
            "retention for %s: pruned %d snapshot(s), expired %d change row(s), "
            "%d old event(s), %d over-cap event(s)",
            connection_id,
            deleted,
            expired,
            events_gone,
            over_cap,
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
    stored; readers filter. An empty diff still marks the log as covering
    this interval (see log_since).

    The coverage marker is written in the same transaction as the rows. Setting
    it first and failing afterwards would leave the connection claiming an
    interval it never stored, and a later scan would not correct it because the
    marker is only written once, so pre-log recovery would skip the interval
    holding the change that caused a finding.
    """
    mark: tuple[str, str] | None = None
    if log_since(connection_id) is None:
        previous = snapshot_summary(from_snapshot_id)
        at = previous.created_at if previous is not None else observed_at
        mark = (f"{LOG_SINCE_KEY}:{connection_id}", json.dumps(at.isoformat()))
    if not changes:
        if mark is not None:
            with db.transaction() as c:
                c.execute(_LOG_SINCE_INSERT, mark)
        return 0
    with db.transaction() as c:
        if mark is not None:
            c.execute(_LOG_SINCE_INSERT, mark)
        c.executemany(
            "INSERT INTO changes(id, connection_id, from_snapshot_id, to_snapshot_id, "
            "observed_at, resource_id, resource_type, resource_name, change_type, "
            "significance, summary, property_changes) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
        where.append("connection_id = %s")
        args.append(connection_id)
    if since is not None:
        where.append("observed_at >= %s")
        args.append(since.isoformat())
    if until is not None:
        where.append("observed_at <= %s")
        args.append(until.isoformat())
    if resource_id:
        where.append("resource_id = %s")
        args.append(resource_id)
    allowed = _SIG_ORDER[: _SIG_ORDER.index(min_significance) + 1]
    where.append("significance = ANY(%s)")
    args.append(list(allowed))
    sig_rank = "CASE significance WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"
    q = (
        "SELECT * FROM changes WHERE "
        + " AND ".join(where)
        + f" ORDER BY observed_at DESC, {sig_rank}, resource_type, resource_name LIMIT %s"
    )
    args.append(limit)
    return [_row_to_change(r) for r in db.fetchall(q, tuple(args))]


def count_changes_by_significance(
    connection_id: str,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, int]:
    """{"high": n, "medium": n, "low": n} for the rows observed inside [since, until]."""
    q = "SELECT significance, COUNT(*) AS n FROM changes WHERE connection_id = %s"
    args: list[object] = [connection_id]
    if since is not None:
        q += " AND observed_at >= %s"
        args.append(since.isoformat())
    if until is not None:
        q += " AND observed_at <= %s"
        args.append(until.isoformat())
    out = {level: 0 for level in _SIG_ORDER}
    for row in db.fetchall(q + " GROUP BY significance", tuple(args)):
        if row["significance"] in out:
            out[row["significance"]] = int(row["n"])
    return out


LOG_SINCE_KEY = "log_since"
LOG_RETAINED_SINCE_KEY = "log_retained_since"


@dataclass(frozen=True)
class LogCoverage:
    since: datetime | None
    overlapping_pair: tuple[str, str] | None = None


def _oldest_change_row(connection_id: str):
    return db.fetchone(
        "SELECT c.from_snapshot_id, c.to_snapshot_id, c.observed_at, "
        "source.id AS source_id, source.created_at AS source_created_at, "
        "target.id AS target_id FROM changes c "
        "LEFT JOIN snapshots source ON source.id = c.from_snapshot_id "
        "AND source.connection_id = c.connection_id "
        "LEFT JOIN snapshots target ON target.id = c.to_snapshot_id "
        "AND target.connection_id = c.connection_id "
        "WHERE c.connection_id = %s ORDER BY c.observed_at ASC LIMIT 1",
        (connection_id,),
    )


def log_since(connection_id: str) -> datetime | None:
    """First covered interval for this connection, including empty diffs."""
    raw = db.get_setting(f"{LOG_SINCE_KEY}:{connection_id}")
    return datetime.fromisoformat(raw) if raw else None


def effective_log_since(connection_id: str) -> datetime | None:
    """Oldest interval still covered after change-log retention."""
    return effective_log_coverage(connection_id).since


def effective_log_coverage(connection_id: str) -> LogCoverage:
    started = log_since(connection_id)
    if started is None:
        return LogCoverage(None)
    raw = db.get_setting(f"{LOG_RETAINED_SINCE_KEY}:{connection_id}")
    retained = datetime.fromisoformat(raw) if raw else started
    since = max(started, retained)
    row = _oldest_change_row(connection_id)
    pair = None
    if (
        row is not None
        and row["source_id"] is None
        and row["target_id"] is not None
        and _dt(row["observed_at"]) == since
    ):
        pair = (row["from_snapshot_id"], row["to_snapshot_id"])
    return LogCoverage(since, pair)


# Both markers are settings rows written through their caller's own transaction
# so they commit with the rows they describe; db.set_setting commits on its own.
#
# log_since is set once and never moved, so a second writer racing the first
# must not overwrite it. log_retained_since only ever moves forward, so the
# comparison is done in SQL rather than read-then-write in Python.
_LOG_SINCE_INSERT = "INSERT INTO settings(key, value) VALUES(%s, %s) ON CONFLICT(key) DO NOTHING"
_RETAINED_SINCE_UPSERT = (
    "INSERT INTO settings(key, value) VALUES(%s, %s) "
    "ON CONFLICT(key) DO UPDATE SET value = GREATEST(settings.value, excluded.value)"
)


def _set_log_since(connection_id: str, at: datetime) -> None:
    db.set_setting(f"{LOG_SINCE_KEY}:{connection_id}", at.isoformat())


def backfill_log_since() -> dict[str, datetime]:
    """Recover each connection's coverage from its oldest surviving change."""
    starts = {}
    for connection in db.fetchall("SELECT DISTINCT connection_id FROM changes"):
        cid = connection["connection_id"]
        start = log_since(cid)
        if start is None:
            row = _oldest_change_row(cid)
            start = datetime.fromisoformat(row["source_created_at"] or row["observed_at"])
            _set_log_since(cid, start)
        starts[cid] = start
    return starts


def count_changes(connection_id: str) -> int:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM changes WHERE connection_id = %s", (connection_id,)
    )
    return int(row["n"])


def prune_changes(connection_id: str, before: datetime) -> int:
    with db.transaction() as c:
        key = f"{LOG_RETAINED_SINCE_KEY}:{connection_id}"
        c.execute(_RETAINED_SINCE_UPSERT, (key, json.dumps(before.isoformat())))
        c.execute(
            "DELETE FROM changes WHERE connection_id = %s AND observed_at < %s",
            (connection_id, before.isoformat()),
        )
        return c.rowcount


# --- findings cache -------------------------------------------------------


def save_findings(snapshot_id: str, findings: list[Finding]) -> None:
    payload = json.dumps([f.model_dump(mode="json") for f in findings])
    with db.transaction() as c:
        c.execute(
            "INSERT INTO findings(snapshot_id, findings) VALUES(%s, %s) "
            "ON CONFLICT(snapshot_id) DO UPDATE SET findings = excluded.findings",
            (snapshot_id, payload),
        )


def findings_cached(snapshot_id: str) -> bool:
    """True when a findings row exists for the snapshot (an empty list still counts)."""
    return (
        db.fetchone("SELECT 1 FROM findings WHERE snapshot_id = %s", (snapshot_id,)) is not None
    )


def _like_literal(value: str) -> str:
    """Escape LIKE wildcards; finding ids are full of underscores."""
    for ch in ("\\", "%", "_"):
        value = value.replace(ch, "\\" + ch)
    return value


def snapshot_ids_with_finding(connection_id: str, finding_id: str) -> set[str]:
    """Snapshots of this connection whose cached findings hold this finding id.

    One query instead of decoding every snapshot's findings blob in Python
    (issue #40). The blobs are json.dumps of a list of findings, so the id
    appears verbatim as `"id": "<finding id>"`; the closing quote keeps one id
    from matching a longer one.
    """
    pattern = f'%"id": "{_like_literal(finding_id)}"%'
    rows = db.fetchall(
        "SELECT f.snapshot_id AS snapshot_id FROM findings f "
        "JOIN snapshots s ON s.id = f.snapshot_id "
        "WHERE s.connection_id = %s AND f.findings LIKE %s ESCAPE '\\'",
        (connection_id, pattern),
    )
    return {r["snapshot_id"] for r in rows}


def get_findings(snapshot_id: str) -> list[Finding]:
    row = db.fetchone("SELECT findings FROM findings WHERE snapshot_id = %s", (snapshot_id,))
    if row is None:
        return []
    return [Finding.model_validate(f) for f in json.loads(row["findings"])]
