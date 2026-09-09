"""Schema migrations.

Numbered `.sql` files in `app/migrations`, applied in order, each in its own
transaction, each recorded in `schema_migrations`. Adding the next one means
dropping a file named `0002_<what_it_does>.sql` beside `0001_initial.sql`; there
is nothing else to register and no file is ever edited after it has shipped.

Two callers run this: the one-shot `migrate` service in docker-compose (or the
equivalent Kubernetes Job) and the app itself at startup. Both take the same
advisory lock, so several workers or pods starting together apply the pending
files once, in order, and the losers wait rather than fail.

Usage:
    python -m app.migrate upgrade   # apply everything pending
    python -m app.migrate status    # print applied and pending revisions
"""

import logging
import sys
from pathlib import Path

import psycopg

from app import db

log = logging.getLogger("vcf_doctor.migrate")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Fixed key: every process that migrates this database takes this one lock.
LOCK_CLASS = db.LOCK_CLASS_SCHEDULER
LOCK_OBJ = 2

_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    revision   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def revisions() -> list[Path]:
    """Every migration file, in the order its name sorts."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _ensure_table() -> None:
    with db.transaction() as c:
        c.execute(_TABLE)


def applied(timeout: float | None = None) -> list[str]:
    """Revisions already applied. A database with no bookkeeping table yet has
    applied none; this is read on every health check, so it never writes."""
    try:
        rows = db.fetchall("SELECT revision FROM schema_migrations ORDER BY 1", timeout=timeout)
    except psycopg.errors.UndefinedTable:
        return []
    return [r["revision"] for r in rows]


def pending(timeout: float | None = None) -> list[Path]:
    done = set(applied(timeout=timeout))
    return [p for p in revisions() if p.stem not in done]


def current() -> str | None:
    """The newest applied revision, or None on an empty database."""
    done = applied()
    return done[-1] if done else None


def upgrade() -> list[str]:
    """Apply every pending migration. Returns the revisions applied here."""
    _ensure_table()
    done: list[str] = []
    with db.pool().connection() as lock_conn:
        # Blocking, not try: a second worker waits for the first to finish
        # rather than racing it or starting against a half-built schema.
        lock_conn.execute("SELECT pg_advisory_lock(%s, %s)", (LOCK_CLASS, LOCK_OBJ))
        try:
            for path in pending():
                log.info("applying migration %s", path.stem)
                with db.transaction() as c:
                    c.execute(path.read_text(encoding="utf-8"))
                    c.execute(
                        "INSERT INTO schema_migrations(revision) VALUES(%s) "
                        "ON CONFLICT DO NOTHING",
                        (path.stem,),
                    )
                done.append(path.stem)
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_CLASS, LOCK_OBJ))
    return done


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else "upgrade"
    if command == "status":
        for revision in applied():
            print(f"applied  {revision}")
        for path in pending():
            print(f"pending  {path.stem}")
        return 0
    if command != "upgrade":
        print(f"usage: python -m app.migrate [upgrade|status]; got {command!r}", file=sys.stderr)
        return 2
    done = upgrade()
    print(f"applied {len(done)} migration(s): {', '.join(done) or 'none pending'}")
    print(f"schema is at {current() or 'empty'}")
    return 0


def _cli() -> int:
    try:
        return main()
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(_cli())
