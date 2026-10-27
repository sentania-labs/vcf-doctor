"""Personal API tokens: a way for a script to act as one signed-in person.

A token is `vcfd_pat_` and 43 random URL-safe characters (256 bits). Only its
SHA-256 is stored, so the secret cannot be read back from the database; it is
returned once, when the token is created. Because the secret is random and not
chosen by a person, a plain fast hash is the right one: there is nothing to
guess and nothing to stretch.

A token belongs to a person (issuer and subject, as in a single sign-on
session) and carries that person's current role: the role is refreshed at every
sign-in, and everything here is refused unless single sign-on is still pointed
at the provider the token's owner came from (app/auth.py checks that). A person
who is refused at a later sign-in loses every token they had.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from app import db

PREFIX = "vcfd_pat_"
MAX_NAME = 100
DEFAULT_DAYS = 30
MAX_DAYS = 365
# Per person, counting only tokens that still work.
MAX_ACTIVE = 25
# last_used_at is a hint for the owner, not an audit record; rewriting the row
# on every request would make a busy script write constantly.
TOUCH_EVERY = timedelta(minutes=5)
# Spent tokens stay listed a while so the owner can see what happened to them.
KEEP_SPENT = timedelta(days=30)

REASON_REVOKED = "revoked"
REASON_REFUSED = "the person was refused at a later sign-in"

_COLUMNS = (
    "id, hint, name, issuer, subject, owner_name, role, "
    "created_at, expires_at, last_used_at, revoked_at, revoked_reason"
)


class TooManyTokens(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def looks_like_token(value: str) -> bool:
    return value.startswith(PREFIX)


def create(
    *, issuer: str, subject: str, owner_name: str, role: str, name: str, days: int
) -> tuple[dict, str]:
    """A new token for this person: its record and its secret (shown once)."""
    secret = PREFIX + secrets.token_urlsafe(32)
    now = _now()
    token_id = uuid.uuid4().hex
    with db.transaction() as c:
        # Two tabs creating at once must not both squeeze under the cap.
        db.lock_in_transaction(c, "api_tokens", f"{issuer}\n{subject}")
        c.execute(
            "DELETE FROM api_tokens WHERE issuer = %s AND subject = %s "
            "AND COALESCE(revoked_at, expires_at) < %s",
            (issuer, subject, _stamp(now - KEEP_SPENT)),
        )
        active = c.execute(
            "SELECT COUNT(*) AS n FROM api_tokens WHERE issuer = %s AND subject = %s "
            "AND revoked_at IS NULL AND expires_at > %s",
            (issuer, subject, _stamp(now)),
        ).fetchone()
        if int(active["n"]) >= MAX_ACTIVE:
            raise TooManyTokens(f"at most {MAX_ACTIVE} active tokens per person")
        c.execute(
            "INSERT INTO api_tokens(id, token_hash, hint, name, issuer, subject, owner_name, "
            "role, created_at, expires_at) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                token_id,
                hash_secret(secret),
                secret[: len(PREFIX) + 4],
                name,
                issuer,
                subject,
                owner_name,
                role,
                _stamp(now),
                _stamp(now + timedelta(days=days)),
            ),
        )
    record = db.fetchone(f"SELECT {_COLUMNS} FROM api_tokens WHERE id = %s", (token_id,))
    assert record is not None
    return record, secret


def list_for(issuer: str, subject: str) -> list[dict]:
    return db.fetchall(
        f"SELECT {_COLUMNS} FROM api_tokens WHERE issuer = %s AND subject = %s "
        "ORDER BY created_at DESC, id",
        (issuer, subject),
    )


def revoke(issuer: str, subject: str, token_id: str) -> bool:
    """End one of this person's tokens. False when they have no such token."""
    with db.transaction() as c:
        row = c.execute(
            "UPDATE api_tokens SET revoked_at = COALESCE(revoked_at, %s), "
            "revoked_reason = COALESCE(revoked_reason, %s) "
            "WHERE id = %s AND issuer = %s AND subject = %s RETURNING id",
            (_stamp(_now()), REASON_REVOKED, token_id, issuer, subject),
        ).fetchone()
    return row is not None


def revoke_all(issuer: str, subject: str, reason: str) -> int:
    """End every token this person still has. Returns how many that was."""
    with db.transaction() as c:
        return c.execute(
            "UPDATE api_tokens SET revoked_at = %s, revoked_reason = %s "
            "WHERE issuer = %s AND subject = %s AND revoked_at IS NULL",
            (_stamp(_now()), reason, issuer, subject),
        ).rowcount


def refresh_role(issuer: str, subject: str, role: str, owner_name: str) -> None:
    """A person signed in: their tokens carry the role and name they have now."""
    with db.transaction() as c:
        c.execute(
            "UPDATE api_tokens SET role = %s, owner_name = %s "
            "WHERE issuer = %s AND subject = %s AND revoked_at IS NULL "
            "AND (role <> %s OR owner_name <> %s)",
            (role, owner_name, issuer, subject, role, owner_name),
        )


def authenticate(secret: str) -> dict | None:
    """The record behind a token secret, or None when it is unknown, revoked or
    expired."""
    if not looks_like_token(secret) or len(secret) > 200:
        return None
    now = _now()
    row = db.fetchone(
        f"SELECT {_COLUMNS} FROM api_tokens WHERE token_hash = %s "
        "AND revoked_at IS NULL AND expires_at > %s",
        (hash_secret(secret), _stamp(now)),
    )
    if row is None:
        return None
    if not row["last_used_at"] or row["last_used_at"] < _stamp(now - TOUCH_EVERY):
        with db.transaction() as c:
            c.execute(
                "UPDATE api_tokens SET last_used_at = %s WHERE id = %s",
                (_stamp(now), row["id"]),
            )
    return row
