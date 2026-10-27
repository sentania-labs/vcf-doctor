"""Service accounts: machine identities that belong to no person.

An administrator makes one with a role, then issues it tokens (app/tokens.py,
`vcfd_sat_`). Rotation without downtime is more than one live token at a time:
issue the new one, move the script over, revoke the old one. Disabling an
account stops every token it has at once (the token check looks at the account),
and enabling it brings back the ones that were neither revoked nor expired.
"""

import uuid
from datetime import UTC, datetime

import psycopg.errors

from app import db

MAX_NAME = 100
MAX_DESCRIPTION = 300

_COLUMNS = "id, name, description, role, created_at, created_by, disabled_at, disabled_by"


class NameTaken(Exception):
    """Another service account already has this name."""


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def create(*, name: str, description: str, role: str, created_by: str) -> dict:
    account_id = uuid.uuid4().hex
    try:
        with db.transaction() as c:
            c.execute(
                "INSERT INTO service_accounts(id, name, description, role, created_at, "
                "created_by) VALUES(%s, %s, %s, %s, %s, %s)",
                (account_id, name, description, role, _stamp(), created_by),
            )
    except psycopg.errors.UniqueViolation:
        raise NameTaken(name) from None
    account = get(account_id)
    assert account is not None
    return account


def _select(where: str, params: tuple) -> list[dict]:
    """Accounts with how many of their tokens could be used right now."""
    columns = ", ".join(f"a.{c.strip()}" for c in _COLUMNS.split(","))
    return db.fetchall(
        f"SELECT {columns}, "
        "(SELECT COUNT(*) FROM api_tokens t WHERE t.service_account_id = a.id "
        "AND t.revoked_at IS NULL AND t.expires_at > %s) AS active_tokens "
        f"FROM service_accounts a {where} ORDER BY a.name, a.id",
        (_stamp(), *params),
    )


def get(account_id: str) -> dict | None:
    rows = _select("WHERE a.id = %s", (account_id,))
    return rows[0] if rows else None


def listing() -> list[dict]:
    return _select("", ())


def set_disabled(account_id: str, disabled: bool, by: str) -> dict | None:
    """Disable or enable an account. Returns it, or None when there is no such
    account. Doing either twice changes nothing the second time."""
    with db.transaction() as c:
        if disabled:
            c.execute(
                "UPDATE service_accounts SET disabled_at = COALESCE(disabled_at, %s), "
                "disabled_by = COALESCE(disabled_by, %s) WHERE id = %s",
                (_stamp(), by, account_id),
            )
        else:
            c.execute(
                "UPDATE service_accounts SET disabled_at = NULL, disabled_by = NULL WHERE id = %s",
                (account_id,),
            )
    return get(account_id)
