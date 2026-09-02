"""Single shared operator password with a signed session cookie.

Deliberately simple: one password, hashed with PBKDF2 and stored in the
settings table; sessions are HMAC-signed timestamps in an HttpOnly cookie.
The signing secret is generated once and kept in the settings table, so it
survives restarts on the /data volume. VCF_DOCTOR_AUTH=off disables all of
it for deployments that front the app with ingress authentication.
"""

import base64
import hashlib
import hmac
import logging
import math
import os
import secrets
import threading
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request

from app import db
from app.config import settings

log = logging.getLogger("vcf_doctor.auth")

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


def set_password(password: str) -> None:
    """Store the password and rotate the signing secret so every existing
    session (including one on a lost laptop) stops working."""
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"password must be at least {MIN_PASSWORD} characters")
    db.set_setting(_HASH_KEY, _hash(password, secrets.token_bytes(16)))
    db.set_setting(_SECRET_KEY, secrets.token_hex(32))


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
        set_password(seed)
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


def issue_token() -> str:
    payload = str(int(time.time())).encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    # Fixed-length signature appended; never split on a delimiter, raw HMAC
    # bytes can contain any value.
    return base64.urlsafe_b64encode(payload + sig).decode()


def token_valid(token: str | None) -> bool:
    if not token or len(token) > 256:
        return False
    try:
        raw = base64.urlsafe_b64decode(token.encode())
        payload, sig = raw[:-_SIG_LEN], raw[-_SIG_LEN:]
        issued = int(payload)
    except (ValueError, TypeError):
        return False
    expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return False
    return time.time() - issued < SESSION_TTL


def is_authenticated(request: Request) -> bool:
    if not enabled():
        return True
    return token_valid(request.cookies.get(COOKIE))


# Paths under /api that never require a session.
PUBLIC_PREFIXES = ("/api/health", "/api/auth/")


def requires_auth(path: str) -> bool:
    return path.startswith("/api/") and not path.startswith(PUBLIC_PREFIXES)


# ---- login backoff --------------------------------------------------------
#
# Keyed per client address (see app/proxies.py for what "client" means behind
# an ingress). Five free failures per address, then an exponential wait
# capped at a minute. On top of that a process-wide ceiling: more than
# GLOBAL_LIMIT failures across every address inside GLOBAL_WINDOW seconds
# pauses logins for everyone, so a guesser rotating addresses still gets no
# more throughput than that. Everything is in memory; the deployment is a
# single replica and a restart simply forgives everyone.

_fail_lock = threading.Lock()
setup_lock = threading.Lock()
_BACKOFF_AFTER = 5
_BACKOFF_MAX = 60
# Bounded store: at most MAX_TRACKED addresses, oldest evicted first, and an
# address whose last failure is older than ENTRY_TTL is forgotten.
MAX_TRACKED = 10_000
ENTRY_TTL = 15 * 60
GLOBAL_LIMIT = 30
GLOBAL_WINDOW = 60

# ip -> [failures, last_failure]; insertion order doubles as LRU order.
_per_ip: OrderedDict[str, list[float]] = OrderedDict()
# Timestamps of the most recent GLOBAL_LIMIT failures, any address.
_recent_failures: deque[float] = deque(maxlen=GLOBAL_LIMIT)


def _sweep(now: float) -> None:
    """Drop expired entries from the front (oldest) until a live one."""
    while _per_ip:
        ip, (_, last) = next(iter(_per_ip.items()))
        if now - last > ENTRY_TTL:
            del _per_ip[ip]
        else:
            break


def _global_wait(now: float) -> float:
    if len(_recent_failures) < GLOBAL_LIMIT:
        return 0.0
    return _recent_failures[0] + GLOBAL_WINDOW - now


def _ip_wait(ip: str, now: float) -> float:
    entry = _per_ip.get(ip)
    if not entry or entry[0] < _BACKOFF_AFTER:
        return 0.0
    failures, last = entry
    wait = min(2 ** (failures - _BACKOFF_AFTER), _BACKOFF_MAX)
    return last + wait - now


def _wait(ip: str, now: float) -> int:
    remaining = max(_ip_wait(ip, now), _global_wait(now))
    return math.ceil(remaining) if remaining > 0 else 0


def _count_failure(ip: str, now: float) -> None:
    _recent_failures.append(now)
    entry = _per_ip.pop(ip, None) or [0, 0.0]
    entry[0] += 1
    entry[1] = now
    _per_ip[ip] = entry  # re-append: most recently active goes last
    _sweep(now)
    while len(_per_ip) > MAX_TRACKED:
        _per_ip.popitem(last=False)


def login_blocked(ip: str) -> int:
    """Seconds this client must wait before another attempt, 0 if allowed."""
    now = time.time()
    with _fail_lock:
        _sweep(now)
        return _wait(ip, now)


def begin_attempt(ip: str) -> tuple[int, float]:
    """Reserve one attempt for this client. Returns (wait, stamp): a
    non-zero wait means refused. Otherwise the attempt is counted as a
    failure right now, before the (slow) password check runs, so a burst
    of concurrent guesses cannot all slip past the counter; finish_attempt
    forgives it on success."""
    now = time.time()
    with _fail_lock:
        _sweep(now)
        wait = _wait(ip, now)
        if wait:
            return wait, 0.0
        _count_failure(ip, now)
        return 0, now


def finish_attempt(ip: str, stamp: float, success: bool) -> None:
    if not success:
        return
    with _fail_lock:
        _per_ip.pop(ip, None)
        try:
            _recent_failures.remove(stamp)
        except ValueError:
            pass  # already rotated out of the window


def record_login(ip: str, success: bool) -> None:
    """One-shot: count a known outcome. begin/finish_attempt is what the
    login endpoint uses; this stays for callers that already know the result."""
    now = time.time()
    with _fail_lock:
        if success:
            _per_ip.pop(ip, None)
        else:
            _count_failure(ip, now)


def tracked_addresses() -> int:
    with _fail_lock:
        return len(_per_ip)


def reset_login_state() -> None:
    """Forget every failure. Tests, and nothing else, call this."""
    global _recent_failures
    with _fail_lock:
        _per_ip.clear()
        _recent_failures = deque(maxlen=GLOBAL_LIMIT)
