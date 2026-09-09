"""PostgreSQL access shared by every backend module.

The schema is owned by the numbered migrations in `app/migrations` and applied
by `app.migrate`; nothing here creates a table.

Concurrency model: a psycopg connection pool per worker process. PostgreSQL,
not this module, serializes writers, so there is no process-wide write lock and
more than one uvicorn worker is supported. The three places that need a writer
to be alone (the scheduler leader, one scan per connection, the startup vault
rewrite) take a PostgreSQL advisory lock, which holds across every worker and
every pod rather than only inside one process.

Timestamps are stored as ISO 8601 UTC text, exactly as the SQLite schema stored
them. Every writer normalises to UTC with a fixed `+00:00` offset, so lexical
ordering and range comparisons are chronological. Keeping the representation
means the port changed the engine and not the time model.
"""

import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import blake2b
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app import config
from app.config import settings as cfg

log = logging.getLogger("vcf_doctor.db")

# Advisory lock namespaces. The two-integer form is used for the scheduler
# lock so `pg_locks` can be queried for it by classid and objid; the
# single-bigint form is used for per-connection scan locks, which nothing
# introspects.
LOCK_CLASS_SCHEDULER = 22083
LOCK_OBJ_SCHEDULER = 1
# Namespace for the per-connection scan lock: one scan of a connection at a
# time across every worker and pod, not merely inside one process.
SCAN_LOCK = "scan"

_pool: ConnectionPool | None = None
_pool_guard = threading.Lock()
_leader: psycopg.Connection | None = None
_leader_guard = threading.Lock()


def conninfo() -> str:
    """libpq connection string: the configured URL plus the password file."""
    url = config.database_url_without_password()
    password = config.database_password()
    if password is None:
        return url
    return make_conninfo(url, password=password)


def _new_pool() -> ConnectionPool:
    pool = ConnectionPool(
        conninfo(),
        min_size=max(0, cfg.db_pool_min_size),
        max_size=max(1, cfg.db_pool_max_size),
        timeout=cfg.db_pool_timeout,
        kwargs={"row_factory": dict_row, "application_name": "vcf-doctor"},
        # A primary that failed over leaves broken sockets in the pool; check
        # each one on the way out so a failover costs a retry, not an error.
        check=ConnectionPool.check_connection,
        open=False,
        name="vcf-doctor",
    )
    pool.open()
    return pool


def pool() -> ConnectionPool:
    global _pool
    with _pool_guard:
        if _pool is None:
            _pool = _new_pool()
        return _pool


def close() -> None:
    """Drop the pool and the scheduler lock connection. Idempotent."""
    global _pool
    release_scheduler_lock()
    with _pool_guard:
        if _pool is not None:
            _pool.close()
            _pool = None


@contextmanager
def transaction() -> Iterator[psycopg.Cursor]:
    """One write transaction: commits on a clean exit, rolls back on error."""
    with pool().connection() as conn, conn.cursor() as cur:
        yield cur


def _params(args: tuple) -> tuple | None:
    """psycopg only scans for placeholders when parameters are given, so a
    query with no arguments and a literal % stays literal."""
    return args if args else None


def fetchone(sql: str, args: tuple = (), timeout: float | None = None) -> dict | None:
    with pool().connection(timeout=timeout) as conn, conn.cursor() as cur:
        return cur.execute(sql, _params(args)).fetchone()


def fetchall(sql: str, args: tuple = (), timeout: float | None = None) -> list[dict]:
    with pool().connection(timeout=timeout) as conn, conn.cursor() as cur:
        return cur.execute(sql, _params(args)).fetchall()


# Reads on the request hot path (the health probe, the trusted-proxies lookup
# every request makes) wait this long rather than the ordinary pool timeout.
# When the database is up the pool answers instantly and this never applies;
# when it is down, a probe that waits ten seconds gets killed and takes a
# still-serving pod with it.
PROBE_TIMEOUT = 1.0


def healthy() -> tuple[bool, str | None]:
    """(usable, error). Usable means reachable and migrated to the newest
    revision; a reachable server missing tables is not a working database.
    Never raises: an unreachable database is an answer, not a failure.
    The Settings database panel reports only the bool."""
    from app import migrate

    try:
        fetchone("SELECT 1 AS ok", timeout=PROBE_TIMEOUT)
        outstanding = [path.stem for path in migrate.pending(timeout=PROBE_TIMEOUT)]
    except Exception as exc:  # noqa: BLE001  any driver or server error is "not healthy"
        return False, str(exc)[:500]
    if outstanding:
        return False, "pending migration(s): " + ", ".join(outstanding)
    return True, None


# --- advisory locks -------------------------------------------------------


def _lock_key(namespace: str, ident: str) -> int:
    """A stable signed 64-bit key for pg_advisory_lock(bigint)."""
    digest = blake2b(f"{namespace}:{ident}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def lock_in_transaction(cur: psycopg.Cursor, namespace: str, ident: str) -> None:
    """Serialize the current transaction against every other one taking the
    same key. Released when the transaction ends, however it ends."""
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (_lock_key(namespace, ident),))


@contextmanager
def try_advisory_lock(namespace: str, ident: str) -> Iterator[bool]:
    """Hold a session advisory lock for the block, or yield False immediately.

    The lock lives on one pooled connection and is released before that
    connection goes back to the pool, so a crashed worker's lock dies with its
    session rather than outliving it.
    """
    key = _lock_key(namespace, ident)
    with pool().connection() as conn:
        got = bool(conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (key,)).fetchone()["ok"])
        try:
            yield got
        finally:
            if got:
                conn.execute("SELECT pg_advisory_unlock(%s)", (key,))


def acquire_scheduler_lock() -> bool:
    """Become the one worker that runs scheduled scans.

    Held on a dedicated connection for the life of the process, so exactly one
    worker (and one pod) schedules even though every worker serves the API.
    """
    global _leader
    with _leader_guard:
        if _leader is not None and not _leader.closed:
            return True
        conn = psycopg.connect(conninfo(), autocommit=True, row_factory=dict_row)
        try:
            got = bool(
                conn.execute(
                    "SELECT pg_try_advisory_lock(%s, %s) AS ok",
                    (LOCK_CLASS_SCHEDULER, LOCK_OBJ_SCHEDULER),
                ).fetchone()["ok"]
            )
        except Exception:
            conn.close()
            raise
        if not got:
            conn.close()
            return False
        _leader = conn
        return True


def scheduler_lock_alive() -> bool:
    """True while this process still holds the lock on a live session.

    A PostgreSQL restart or a failover ends the session and takes the lock with
    it, silently. The scheduler asks this every minute so it can notice and
    take the lock again rather than quietly stopping.
    """
    global _leader
    with _leader_guard:
        if _leader is None:
            return False
        try:
            row = _leader.execute(
                "SELECT 1 AS held FROM pg_locks WHERE locktype = 'advisory' "
                "AND classid = %s AND objid = %s AND granted AND pid = pg_backend_pid()",
                (LOCK_CLASS_SCHEDULER, LOCK_OBJ_SCHEDULER),
            ).fetchone()
        except Exception:  # noqa: BLE001  a dead session is simply not holding it
            try:
                _leader.close()
            except Exception:  # noqa: BLE001
                pass
            _leader = None
            return False
        return row is not None


def release_scheduler_lock() -> None:
    global _leader
    with _leader_guard:
        if _leader is not None:
            try:
                _leader.close()
            except Exception:  # noqa: BLE001  shutting down; a dead socket is fine
                pass
            _leader = None


def scheduler_lock_held() -> bool:
    """True when any worker in the deployment holds the scheduler lock.

    Asked from a worker that is not the leader, this still answers "scheduled
    scans are running", so /api/health and Settings do not depend on which
    worker served the request.
    """
    try:
        row = fetchone(
            "SELECT 1 AS held FROM pg_locks WHERE locktype = 'advisory' "
            "AND classid = %s AND objid = %s AND granted LIMIT 1",
            (LOCK_CLASS_SCHEDULER, LOCK_OBJ_SCHEDULER),
            timeout=PROBE_TIMEOUT,
        )
    except Exception:  # noqa: BLE001  an unreachable database is not a running scheduler
        return False
    return row is not None


# --- settings key/value ---------------------------------------------------

# Written through a caller's own transaction where a settings row has to commit
# with the rows it describes; db.set_setting commits on its own.
SETTING_UPSERT = (
    "INSERT INTO settings(key, value) VALUES(%s, %s) "
    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
)


def get_setting(key: str, default: Any = None, timeout: float | None = None) -> Any:
    row = fetchone("SELECT value FROM settings WHERE key = %s", (key,), timeout=timeout)
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value: Any) -> None:
    with transaction() as c:
        c.execute(SETTING_UPSERT, (key, json.dumps(value)))


# --- test support ---------------------------------------------------------


TEST_DATABASE_URL_ENV = "VCF_DOCTOR_TEST_DATABASE_URL"


def reset_for_tests() -> None:
    """Empty the test database and reapply the migrations. Tests only.

    This drops a schema, so it refuses to run unless the environment names the
    database it may do that to. Nothing here reads the deployment's own
    VCF_DOCTOR_DATABASE_URL.
    """
    import os

    from app import migrate

    if not os.environ.get(TEST_DATABASE_URL_ENV, "").strip():
        raise RuntimeError(
            f"reset_for_tests drops the schema and needs {TEST_DATABASE_URL_ENV} set; "
            "run the suite through `make test`"
        )
    close()
    with transaction() as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
    migrate.upgrade()


def reconnect_for_tests() -> None:
    """Drop the pool but keep the data, standing in for a process restart."""
    close()
