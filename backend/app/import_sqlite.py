"""One-shot import of an existing SQLite database into PostgreSQL.

An upgrade keeps its history: point this at the `vcf-doctor.db` file on the old
volume and every connection, schedule, snapshot, finding, change and event moves
across. It is a one-way, one-time move. Nothing writes back to the SQLite file,
and the app never reads one again.

Run it against a migrated but empty PostgreSQL database:

    python -m app.migrate upgrade
    python -m app.import_sqlite --path /data/vcf-doctor.db

It refuses a target that already holds history unless `--force` is given, so a
second accidental run cannot double one. Settings do not count as history: the
console seeds a few rows the moment it starts, and the upgrading deployment's
own settings, its operator password included, replace them.

Encrypted values (vCenter passwords, the Anthropic key) are copied as they are.
They stay readable only if the deployment keeps the same encryption key, which
is the same rule as moving the old volume anywhere else.
"""

import argparse
import gzip
import logging
import sqlite3
import sys
from pathlib import Path

from app import db

log = logging.getLogger("vcf_doctor.import")

# Tables in the order they must be inserted (schedules and findings reference
# connections and snapshots). event_maintenance is deliberately absent: it held
# SQLite vacuum bookkeeping and PostgreSQL's autovacuum replaces it.
TABLES = (
    "settings",
    "connections",
    "schedules",
    "scan_runs",
    "snapshots",
    "findings",
    "changes",
    "events",
    "event_capture_state",
    "event_incomplete_intervals",
)

# Integer flags in SQLite, real booleans in PostgreSQL.
BOOLEAN_COLUMNS = {
    ("connections", "verify_tls"),
    ("schedules", "enabled"),
    ("snapshots", "scheduled"),
    ("event_capture_state", "task_history_unavailable"),
}

# Settings rows that described the SQLite file itself and mean nothing here.
SKIPPED_SETTINGS = ("incremental_compaction_migration",)

# Columns of each target table, in the order values are bound below.
TARGET_COLUMNS = {
    "settings": ("key", "value"),
    "connections": (
        "id",
        "name",
        "host",
        "username",
        "password",
        "verify_tls",
        "kind",
        "created_at",
    ),
    "schedules": (
        "connection_id",
        "interval_minutes",
        "enabled",
        "last_run",
        "next_run",
        "last_status",
    ),
    "scan_runs": (
        "id",
        "connection_id",
        "started",
        "finished",
        "status",
        "error",
        "snapshot_id",
        "trigger",
    ),
    "snapshots": (
        "id",
        "connection_id",
        "created_at",
        "label",
        "scheduled",
        "resource_count",
        "resources_gz",
    ),
    "findings": ("snapshot_id", "findings"),
    "changes": (
        "id",
        "connection_id",
        "from_snapshot_id",
        "to_snapshot_id",
        "observed_at",
        "resource_id",
        "resource_type",
        "resource_name",
        "change_type",
        "significance",
        "summary",
        "property_changes",
    ),
    "events": (
        "id",
        "connection_id",
        "time",
        "source",
        "type",
        "category",
        "message",
        "user",
        "resource_id",
        "resource_name",
        "resource_type",
    ),
    "event_capture_state": ("connection_id", "last_complete_end", "task_history_unavailable"),
    "event_incomplete_intervals": (
        "id",
        "connection_id",
        "since",
        "until",
        "attempts",
        "last_error",
        "updated_at",
    ),
}

# What "already imported" is judged on. `settings` is excluded on purpose: the
# console writes a session secret, an operator password and the default event
# policy on its first start, so a target that has ever been opened is never
# empty by that measure.
HISTORY_TABLES = tuple(t for t in TABLES if t != "settings")

BATCH = 200


def _quoted(table: str, column: str) -> str:
    """`time` and `user` are PostgreSQL keywords and are always quoted."""
    return f'"{column}"' if column in ("time", "user") else column


def _sqlite_tables(source: sqlite3.Connection) -> set[str]:
    rows = source.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r["name"] for r in rows}


def _sqlite_columns(source: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in source.execute(f"PRAGMA table_info({table})")}


def target_row_counts() -> dict[str, int]:
    """How many rows of history each target table already holds."""
    return {
        table: int(db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")["n"])
        for table in HISTORY_TABLES
    }


def _target_columns(table: str, present: set[str]) -> tuple[str, ...]:
    """The target columns this source table can fill. A column the source never
    had is left out of the INSERT so the target's own default applies; a volume
    written before `kind`, `resource_count`, `enabled` or
    `task_history_unavailable` existed is exactly what this importer is for, and
    those four targets are NOT NULL."""

    def available(column: str) -> bool:
        if table == "snapshots" and column == "resources_gz":
            return bool(present & {"resources_gz", "resources"})
        return column in present

    return tuple(column for column in TARGET_COLUMNS[table] if available(column))


def _value(table: str, column: str, row: sqlite3.Row):
    raw = row[column]
    if (table, column) in BOOLEAN_COLUMNS:
        return bool(raw)
    return raw


def _snapshot_blob(row: sqlite3.Row, present: set[str]) -> bytes | None:
    """The compressed resources, compressing a legacy text row on the way past.

    Databases written before the gzip change hold the resource list as JSON
    text; there is no text column here, so it is compressed as it moves.
    """
    blob = row["resources_gz"] if "resources_gz" in present else None
    if blob:
        return bytes(blob)
    text = row["resources"] if "resources" in present else None
    if not text:
        return None
    return gzip.compress(text.encode("utf-8"), compresslevel=6)


def import_table(source: sqlite3.Connection, table: str) -> int:
    present = _sqlite_columns(source, table)
    columns = _target_columns(table, present)
    placeholders = ",".join(["%s"] * len(columns))
    names = ",".join(_quoted(table, c) for c in columns)
    # The old deployment's settings win, so an upgrade keeps its operator
    # password, retention policy and health weights rather than the defaults the
    # fresh instance wrote when it first started. History rows are immutable and
    # only ever collide on a re-run, so those are left alone.
    conflict = (
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        if table == "settings"
        else "ON CONFLICT DO NOTHING"
    )
    sql = f"INSERT INTO {table}({names}) VALUES({placeholders}) {conflict}"
    cursor = source.execute(f"SELECT * FROM {table}")
    moved = 0
    while True:
        rows = cursor.fetchmany(BATCH)
        if not rows:
            break
        payload = []
        for row in rows:
            if table == "settings" and row["key"] in SKIPPED_SETTINGS:
                continue
            if table == "snapshots":
                payload.append(
                    tuple(
                        _snapshot_blob(row, present)
                        if column == "resources_gz"
                        else _value(table, column, row)
                        for column in columns
                    )
                )
                continue
            payload.append(tuple(_value(table, column, row) for column in columns))
        if not payload:
            continue
        with db.transaction() as c:
            c.executemany(sql, payload)
        moved += len(payload)
        log.info("%s: %d rows", table, moved)
    return moved


def _resync_identity() -> None:
    """Move the incomplete-intervals identity past the ids that were imported,
    so the next generated id does not collide with one that came across."""
    with db.transaction() as c:
        c.execute(
            "SELECT setval(pg_get_serial_sequence('event_incomplete_intervals', 'id'), "
            "COALESCE((SELECT MAX(id) FROM event_incomplete_intervals), 0) + 1, false)"
        )


def run(path: Path, force: bool = False) -> dict[str, int]:
    """Copy every table across. Returns rows moved per table."""
    if not path.is_file():
        raise FileNotFoundError(f"no SQLite database at {path}")
    existing = {table: n for table, n in target_row_counts().items() if n}
    if existing and not force:
        summary = ", ".join(f"{table}={n}" for table, n in sorted(existing.items()))
        raise SystemExit(
            f"the target database already holds history ({summary}). Import into a "
            "database that has none, or pass --force to add these rows to what is "
            "already there."
        )
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        available = _sqlite_tables(source)
        moved = {}
        for table in TABLES:
            if table not in available:
                log.info("%s: not present in the SQLite database, skipped", table)
                moved[table] = 0
                continue
            moved[table] = import_table(source, table)
    finally:
        source.close()
    _resync_identity()
    return moved


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", required=True, help="path to the old vcf-doctor.db file")
    parser.add_argument(
        "--force",
        action="store_true",
        help="import even though the target database already holds rows",
    )
    args = parser.parse_args(argv)
    moved = run(Path(args.path), force=args.force)
    for table, count in moved.items():
        print(f"{table}: {count}")
    print(f"imported {sum(moved.values())} rows from {args.path}")
    return 0


def _cli() -> int:
    try:
        return main()
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(_cli())
