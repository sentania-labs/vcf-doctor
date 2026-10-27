"""The schema migration runner: what is applied, what is pending, and that a
second migration is an ordinary file drop rather than a special case."""

import pytest

from app import db, migrate


@pytest.fixture(autouse=True)
def _fresh_db():
    db.reset_for_tests()


def _drop_schema() -> None:
    with db.transaction() as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")


def test_initial_revision_is_applied_and_reported():
    assert migrate.current() == "0003_api_tokens"
    assert migrate.pending() == []
    assert migrate.applied() == ["0001_initial", "0002_login_lockout", "0003_api_tokens"]


def test_upgrade_is_idempotent():
    assert migrate.upgrade() == []
    assert migrate.current() == "0003_api_tokens"


def test_upgrade_works_with_a_single_pool_connection(monkeypatch):
    from app.config import settings

    _drop_schema()
    db.close()
    monkeypatch.setattr(settings, "db_pool_min_size", 1)
    monkeypatch.setattr(settings, "db_pool_max_size", 1)
    monkeypatch.setattr(settings, "db_pool_timeout", 0.2)

    assert migrate.upgrade() == ["0001_initial", "0002_login_lockout", "0003_api_tokens"]
    assert migrate.current() == "0003_api_tokens"


def test_every_table_the_app_writes_exists():
    rows = db.fetchall(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    names = {r["table_name"] for r in rows}
    assert {
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
        "login_failures",
        "login_failure_window",
        "schema_migrations",
    } <= names
    # The SQLite vacuum bookkeeping table is not carried over.
    assert "event_maintenance" not in names


def test_a_second_migration_is_just_another_file(tmp_path, monkeypatch):
    """The next change to the schema (per-user accounts, say) adds one numbered
    file and nothing else. Applying it must not need the first one rewritten."""
    for path in migrate.revisions():
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "0004_example.sql").write_text(
        "CREATE TABLE example_later (id TEXT PRIMARY KEY);", encoding="utf-8"
    )
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

    assert [p.stem for p in migrate.pending()] == ["0004_example"]
    assert migrate.upgrade() == ["0004_example"]
    assert migrate.current() == "0004_example"
    assert db.fetchone("SELECT COUNT(*) AS n FROM example_later")["n"] == 0

    _drop_schema()
    # A fresh database applies all revisions in order, so the initial revision
    # is not a first-install special case.
    expected = ["0001_initial", "0002_login_lockout", "0003_api_tokens", "0004_example"]
    assert migrate.upgrade() == expected
    assert migrate.current() == "0004_example"


def test_database_is_unhealthy_while_a_migration_is_pending(tmp_path, monkeypatch):
    """A reachable server missing its tables is not a usable database, and the
    Settings panel has to say so."""
    assert db.healthy() == (True, None)
    for path in migrate.revisions():
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "0004_example.sql").write_text(
        "CREATE TABLE example_later (id TEXT PRIMARY KEY);", encoding="utf-8"
    )
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)
    ok, error = db.healthy()
    assert ok is False
    assert "0004_example" in error
