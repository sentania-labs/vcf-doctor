"""Sessions: a shared operator password, and optionally single sign-on.

Deliberately simple: one password, hashed with PBKDF2 and stored in the
settings table; sessions are HMAC-signed tokens in an HttpOnly cookie. A
password session is a signed timestamp. A single sign-on session (app/oidc.py)
is a signed record that also says who signed in: issuer, subject and display
name. The signing secret is generated once and kept in the settings table, so
it survives restarts and is the same in every worker. The operator password
stays as break-glass whether or not single sign-on is configured.
VCF_DOCTOR_AUTH=off disables all of it for deployments that front the app with
ingress authentication.
"""

import base64
import hashlib
import hmac
import json
import logging
import math
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app import db, oidc
from app.config import settings

log = logging.getLogger("vcf_doctor.auth")
# Who did what. Sign-ins, refusals, sign-outs and every change made through the
# API are written here with the person's identity (see main.py for the setup).
audit = logging.getLogger("vcf_doctor.audit")

COOKIE = "vcfdoctor_session"
_SIG_LEN = 32  # sha256 digest length
SESSION_TTL = 7 * 24 * 3600
MIN_PASSWORD = 8
_HASH_KEY = "auth_password_hash"
_SECRET_KEY = "auth_session_secret"
_ITER = 200_000


def enabled() -> bool:
    return settings.auth.lower() not in ("off", "false", "0", "disabled")


def configured() -> bool:
    return bool(db.get_setting(_HASH_KEY))


def _hash(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def _password_settings(password: str) -> dict[str, str]:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"password must be at least {MIN_PASSWORD} characters")
    return {
        _HASH_KEY: _hash(password, secrets.token_bytes(16)),
        _SECRET_KEY: secrets.token_hex(32),
    }


def set_password(password: str) -> None:
    """Store the password and rotate the signing secret so every existing
    session (including one on a lost laptop) stops working."""
    db.set_settings(_password_settings(password))


def set_initial_password(password: str) -> bool:
    return db.initialize_settings(_HASH_KEY, _password_settings(password))


def verify_password(password: str) -> bool:
    stored = db.get_setting(_HASH_KEY)
    if not stored:
        return False
    try:
        _, salt_b64, _ = stored.split("$", 2)
    except ValueError:
        return False
    candidate = _hash(password, base64.b64decode(salt_b64))
    return hmac.compare_digest(candidate, stored)


def bootstrap_from_env() -> None:
    """VCF_DOCTOR_ADMIN_PASSWORD seeds the password on first boot only."""
    if not enabled():
        log.warning("authentication is disabled (VCF_DOCTOR_AUTH=%s)", settings.auth)
        return
    if configured():
        return
    seed = os.environ.get("VCF_DOCTOR_ADMIN_PASSWORD", "")
    if seed and len(seed) < MIN_PASSWORD:
        log.warning(
            "VCF_DOCTOR_ADMIN_PASSWORD is shorter than %d characters and was ignored",
            MIN_PASSWORD,
        )
    elif seed:
        set_initial_password(seed)
        return
    log.warning(
        "no operator password set; the first visitor to the UI will be asked to set one. "
        "Set VCF_DOCTOR_ADMIN_PASSWORD for any deployment reachable by more than one person."
    )


def _secret() -> bytes:
    s = db.get_setting(_SECRET_KEY)
    if not s:
        s = secrets.token_hex(32)
        db.set_setting(_SECRET_KEY, s)
    return s.encode()


# Tokens carry their own signature at the end, a fixed 32 bytes; never split on
# a delimiter, raw HMAC bytes can contain any value. The longest legitimate
# token (a sign-in flow that has just started) is a couple of kilobytes.
_MAX_TOKEN = 4096


def _seal(payload: bytes) -> str:
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + sig).decode()


def _unseal(token: str | None) -> bytes | None:
    """The payload of a token this deployment signed, or None."""
    if not token or len(token) > _MAX_TOKEN:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode())
    except (ValueError, TypeError):
        return None
    payload, sig = raw[:-_SIG_LEN], raw[-_SIG_LEN:]
    expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return payload if hmac.compare_digest(sig, expected) else None


@dataclass(frozen=True)
class Session:
    """Who a valid session cookie belongs to. A password session is the shared
    operator; a single sign-on session is one person."""

    method: str  # "password" or "oidc"
    issued: int
    issuer: str = ""
    subject: str = ""
    name: str = ""
    role: str = ""

    @property
    def sso(self) -> bool:
        return self.method == "oidc"

    def describe(self) -> str:
        """For logs. The name is quoted, so a hostile one cannot forge a line."""
        if not self.sso:
            return "operator password"
        return f"{self.name!r} (sub={self.subject}, iss={self.issuer})"


def issue_token(identity: oidc.Identity | None = None) -> str:
    """A session cookie value: for the operator password, or for a person who
    signed in through the identity provider."""
    now = int(time.time())
    if identity is None:
        return _seal(str(now).encode())
    record = {
        "v": 2,
        "m": "oidc",
        "iat": now,
        "iss": identity.issuer,
        "sub": identity.subject,
        "name": identity.name,
        "role": identity.role,
    }
    return _seal(json.dumps(record, separators=(",", ":")).encode())


def read_token(token: str | None) -> Session | None:
    payload = _unseal(token)
    if payload is None:
        return None
    try:
        if payload.startswith(b"{"):
            record = json.loads(payload)
            if record.get("v") != 2 or record.get("m") != "oidc":
                return None
            session = Session(
                method="oidc",
                issued=int(record["iat"]),
                issuer=str(record["iss"]),
                subject=str(record["sub"]),
                name=str(record.get("name", "")),
                role=str(record.get("role", "")),
            )
        else:
            session = Session(method="password", issued=int(payload))
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    if time.time() - session.issued >= SESSION_TTL:
        return None
    if session.sso and (session.role != oidc.ROLE_ADMIN or not oidc.accepts_issuer(session.issuer)):
        # Single sign-on was turned off, or pointed at another provider, since
        # this person signed in: the session is not honoured any more.
        return None
    return session


def seal_flow(flow: dict) -> str:
    """The cookie that carries a sign-in attempt (state, nonce, PKCE verifier)
    from the redirect to the provider to the redirect back. Signed so it cannot
    be forged; it is not a session and read_token refuses it."""
    return _seal(json.dumps({"p": "flow", **flow}, separators=(",", ":")).encode())


def unseal_flow(token: str | None) -> dict | None:
    payload = _unseal(token)
    if payload is None or not payload.startswith(b"{"):
        return None
    try:
        record = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(record, dict) or record.pop("p", None) != "flow":
        return None
    return record


def token_valid(token: str | None) -> bool:
    return read_token(token) is not None


def session_for(request: Request) -> Session | None:
    """The session behind this request's cookie, None without one. With
    authentication off there are no sessions to read."""
    if not enabled():
        return None
    return read_token(request.cookies.get(COOKIE))


# Paths under /api that never require a session.
PUBLIC_PREFIXES = ("/api/health", "/api/version", "/api/auth/")


def requires_auth(path: str) -> bool:
    return path.startswith("/api/") and not path.startswith(PUBLIC_PREFIXES)


# ---- login backoff --------------------------------------------------------
#
# Keyed per client address (see app/proxies.py for what "client" means behind
# an ingress) and shared by every endpoint that checks a password: /auth/login
# and /auth/change. Five free failures per address, then an exponential wait
# capped at a minute. On top of that a deployment-wide ceiling: more than
# GLOBAL_LIMIT failures across every address inside GLOBAL_WINDOW seconds
# pauses logins for everyone, so a guesser rotating addresses still gets no
# more throughput than that. Both counters live in PostgreSQL and are
# serialized under a transaction advisory lock, so every worker and replica
# shares one allowance and a process restart does not reset it.

_LOCK_NS = "auth"
_LOCK_KEY = "lockout"
_BACKOFF_AFTER = 5
_BACKOFF_MAX = 60
# Bounded store: at most MAX_TRACKED addresses, oldest evicted first, and an
# address whose last failure is older than ENTRY_TTL is forgotten.
MAX_TRACKED = 10_000
ENTRY_TTL = 15 * 60
GLOBAL_LIMIT = 30
GLOBAL_WINDOW = 60


def _sweep(c, now: float) -> None:
    """Drop expired per-address entries and trim the global failure window."""
    c.execute(
        "DELETE FROM login_failures WHERE (%s - last_failure) > %s",
        (now, float(ENTRY_TTL)),
    )
    c.execute(
        "DELETE FROM login_failure_window "
        "WHERE (%s - occurred_at) >= %s "
        "OR id NOT IN ("
        "SELECT id FROM login_failure_window ORDER BY occurred_at DESC, id DESC LIMIT %s"
        ")",
        (now, float(GLOBAL_WINDOW), max(0, GLOBAL_LIMIT)),
    )


def _global_wait(c, now: float) -> float:
    row = c.execute(
        "SELECT COUNT(*) AS n, MIN(occurred_at) AS oldest FROM login_failure_window"
    ).fetchone()
    if not row or int(row["n"]) < GLOBAL_LIMIT or row["oldest"] is None:
        return 0.0
    return float(row["oldest"]) + GLOBAL_WINDOW - now


def _ip_wait(c, ip: str, now: float) -> float:
    row = c.execute(
        "SELECT failures, last_failure FROM login_failures WHERE ip = %s",
        (ip,),
    ).fetchone()
    if not row or int(row["failures"]) < _BACKOFF_AFTER:
        return 0.0
    failures = int(row["failures"])
    last = float(row["last_failure"])
    exp = min(failures - _BACKOFF_AFTER, 10)
    wait = min(2**exp, _BACKOFF_MAX)
    return last + wait - now


def _wait(c, ip: str, now: float) -> int:
    remaining = max(_ip_wait(c, ip, now), _global_wait(c, now))
    return math.ceil(remaining) if remaining > 0 else 0


def _count_failure(c, ip: str, now: float) -> None:
    c.execute("INSERT INTO login_failure_window(occurred_at) VALUES(%s)", (now,))
    c.execute(
        "INSERT INTO login_failures(ip, failures, last_failure) VALUES(%s, 1, %s) "
        "ON CONFLICT(ip) DO UPDATE SET "
        "failures = login_failures.failures + 1, "
        "last_failure = excluded.last_failure, "
        "updated_seq = excluded.updated_seq",
        (ip, now),
    )
    _sweep(c, now)
    c.execute(
        "DELETE FROM login_failures WHERE ip NOT IN ("
        "SELECT ip FROM login_failures ORDER BY last_failure DESC, updated_seq DESC LIMIT %s"
        ")",
        (max(0, MAX_TRACKED),),
    )


def too_many_response(wait: int) -> JSONResponse:
    """The 429 every password check returns when the limiter refuses a client."""
    return JSONResponse(
        {"detail": f"too many failed attempts; try again in {wait}s", "retry_after": wait},
        status_code=429,
        headers={"Retry-After": str(wait)},
    )


def login_blocked(ip: str) -> int:
    """Seconds this client must wait before another attempt, 0 if allowed."""
    now = time.time()
    with db.transaction() as c:
        db.lock_in_transaction(c, _LOCK_NS, _LOCK_KEY)
        _sweep(c, now)
        return _wait(c, ip, now)


def begin_attempt(ip: str) -> tuple[int, float]:
    """Reserve one attempt for this client. Returns (wait, stamp): a
    non-zero wait means refused. Otherwise the attempt is counted as a
    failure right now, before the (slow) password check runs, so a burst
    of concurrent guesses cannot all slip past the counter; finish_attempt
    forgives it on success."""
    now = time.time()
    with db.transaction() as c:
        db.lock_in_transaction(c, _LOCK_NS, _LOCK_KEY)
        _sweep(c, now)
        wait = _wait(c, ip, now)
        if wait:
            return wait, 0.0
        _count_failure(c, ip, now)
        return 0, now


def finish_attempt(ip: str, stamp: float, success: bool) -> None:
    if not success:
        return
    with db.transaction() as c:
        db.lock_in_transaction(c, _LOCK_NS, _LOCK_KEY)
        c.execute("DELETE FROM login_failures WHERE ip = %s", (ip,))
        c.execute(
            "DELETE FROM login_failure_window WHERE id = ("
            "SELECT id FROM login_failure_window WHERE occurred_at = %s ORDER BY id DESC LIMIT 1"
            ")",
            (stamp,),
        )


def record_login(ip: str, success: bool) -> None:
    """One-shot: count a known outcome. begin/finish_attempt is what the
    login and password-change endpoints use; this stays for callers that
    already know the result."""
    now = time.time()
    with db.transaction() as c:
        db.lock_in_transaction(c, _LOCK_NS, _LOCK_KEY)
        if success:
            c.execute("DELETE FROM login_failures WHERE ip = %s", (ip,))
        else:
            _count_failure(c, ip, now)


def tracked_addresses() -> int:
    row = db.fetchone("SELECT COUNT(*) AS n FROM login_failures")
    return int(row["n"]) if row else 0


def recent_failures() -> int:
    row = db.fetchone("SELECT COUNT(*) AS n FROM login_failure_window")
    return int(row["n"]) if row else 0


def reset_login_state() -> None:
    """Forget every failure. Tests, and nothing else, call this."""
    with db.transaction() as c:
        db.lock_in_transaction(c, _LOCK_NS, _LOCK_KEY)
        c.execute("DELETE FROM login_failures")
        c.execute("DELETE FROM login_failure_window")
