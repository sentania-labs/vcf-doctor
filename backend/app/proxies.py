"""Trusted proxies: who is allowed to tell us the real client address.

The app runs behind an ingress, so the TCP peer of every request is the
ingress, not the person at the keyboard. The ingress adds X-Forwarded-For
and X-Forwarded-Proto, but so can anyone who reaches the pod directly, so
those headers are only believed when the peer is in the trusted list.

The list is edited on the Settings page (stored in the database) and can be
pinned by VCF_DOCTOR_TRUSTED_PROXIES (comma-separated IPs or CIDRs), which
wins over the stored value. Default: empty, trust nobody. With nothing
trusted every request behind the ingress looks like it comes from the
ingress, which is today's behaviour: one login bucket shared by everyone.
"""

import ipaddress
import logging
import threading
import time
from typing import Any

from app import db
from app.config import settings

log = logging.getLogger("vcf_doctor.proxies")

SETTING_KEY = "trusted_proxies"

Network = ipaddress.IPv4Network | ipaddress.IPv6Network

# The stored list is read by the outermost middleware on every request, and
# against PostgreSQL that is a network round trip rather than a local page read.
# The liveness answer sits behind this middleware and has to be given while the
# database is unreachable, so no request ever waits for the read: the answer
# comes from memory and a missing or expired entry is refreshed on a background
# thread. A cold cache trusts nobody, which is the safe default and costs at
# most one visitor sharing the ingress's login lockout until the refresh lands.
CACHE_TTL = 5.0
_cache: list[str] | None = None
_cached_at = 0.0
_refreshing = False
_cache_guard = threading.Lock()


def reset_cache() -> None:
    """Forget the cached list. Called when the test database is rebuilt under a
    running process."""
    global _cache, _cached_at
    with _cache_guard:
        _cache = None
        _cached_at = 0.0


def _remember(value: list[str]) -> None:
    global _cache, _cached_at
    with _cache_guard:
        _cache = value
        _cached_at = time.monotonic()


def _refresh_stored() -> None:
    """Read the list and remember it, off the request path. A failed read is
    remembered as "trust nobody" like any other answer, so an outage cannot make
    the next request wait; the read is retried once the entry expires."""
    global _refreshing
    try:
        try:
            value = parse_list(db.get_setting(SETTING_KEY, timeout=db.PROBE_TIMEOUT) or [])
        except ValueError:
            value = []
        except Exception:  # noqa: BLE001  a database that cannot be read trusts nobody
            log.warning("trusted proxies unreadable; trusting nobody until the database returns")
            value = []
        _remember(value)
    finally:
        with _cache_guard:
            _refreshing = False


def parse_list(raw: Any) -> list[str]:
    """Normalise user input (a list, or a comma/space separated string) into
    canonical network strings. Raises ValueError naming the bad entry."""
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [p for p in raw.replace(",", " ").split() if p]
    elif isinstance(raw, list | tuple):
        items = [str(p).strip() for p in raw if str(p).strip()]
    else:
        raise ValueError("trusted proxies must be a list of IPs or CIDRs")
    out: list[str] = []
    for item in items:
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError as exc:
            raise ValueError(f"'{item}' is not an IP address or CIDR range") from exc
        if net.prefixlen == 0:
            # Trusting everyone means every hop is "trusted" and the client
            # address becomes whatever the visitor wrote in the header.
            raise ValueError(f"'{item}' would trust every address; list the ingress only")
        text = str(net)
        if text not in out:
            out.append(text)
    return out


def env_value() -> list[str] | None:
    """The VCF_DOCTOR_TRUSTED_PROXIES override, or None when unset or empty.
    A malformed value fails closed: it pins the list to empty (trust nobody)
    rather than falling back to whatever the Settings page stored, and the
    bad entry is reported on the Settings page."""
    raw = (settings.trusted_proxies or "").strip()
    if not raw:
        return None
    try:
        return parse_list(raw)
    except ValueError:
        return []


def env_problem() -> str | None:
    raw = (settings.trusted_proxies or "").strip()
    if not raw:
        return None
    try:
        parse_list(raw)
    except ValueError as exc:
        return f"VCF_DOCTOR_TRUSTED_PROXIES ignored: {exc}"
    return None


def stored_value() -> list[str]:
    """The saved list, answered from memory and never by waiting.

    This runs in the outermost middleware, on every request including the health
    endpoints and the login page, so it returns what is remembered and starts a
    refresh in the background when that is missing or older than CACHE_TTL.
    Nothing here can block a response, which is what lets liveness answer in
    milliseconds while PostgreSQL is unreachable, and an unreachable database
    trusts nobody rather than turning every response into a 500.
    """
    global _refreshing
    with _cache_guard:
        value = _cache
        expired = value is None or time.monotonic() - _cached_at >= CACHE_TTL
        refresh = expired and not _refreshing
        if refresh:
            _refreshing = True
    if refresh:
        threading.Thread(target=_refresh_stored, name="trusted-proxies", daemon=True).start()
    return value if value is not None else []


def effective() -> tuple[list[str], str]:
    """(networks, source) where source is "env" or "settings"."""
    env = env_value()
    if env is not None:
        return env, "env"
    return stored_value(), "settings"


def set_stored(raw: Any) -> list[str]:
    value = parse_list(raw)
    db.set_setting(SETTING_KEY, value)
    _remember(value)
    return value


def networks() -> list[Network]:
    return [ipaddress.ip_network(n) for n in effective()[0]]


def is_trusted(host: str | None, nets: list[Network]) -> bool:
    if not host or not nets:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False  # "testclient", unix sockets: never a proxy
    # A dual-stack listener reports IPv4 peers as ::ffff:a.b.c.d.
    mapped = getattr(addr, "ipv4_mapped", None)
    return any(addr in n or (mapped is not None and mapped in n) for n in nets)


def forwarded_headers_present(request) -> bool:
    return "x-forwarded-for" in request.headers or "x-forwarded-proto" in request.headers


# Untrusted peers whose forwarded headers were ignored, logged once each so
# an operator finds the ingress address in the log without being flooded.
_warned_peers: set[str] = set()
_WARNED_MAX = 32


def _split_forwarded_for(values: list[str]) -> list[str]:
    hosts: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                hosts.append(part)
    return hosts


def _strip_port(host: str) -> str:
    """X-Forwarded-For entries occasionally carry a port; drop it."""
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    if host.count(":") == 1:
        return host.rsplit(":", 1)[0]
    return host


def resolve_client(peer: str | None, forwarded_for: list[str], nets: list[Network]) -> str | None:
    """The client address: the rightmost X-Forwarded-For entry that is not a
    trusted proxy, when the peer is trusted; otherwise the peer itself. An
    untrusted peer's headers are ignored entirely."""
    if not is_trusted(peer, nets):
        return peer
    hosts = [_strip_port(h) for h in _split_forwarded_for(forwarded_for)]
    if not hosts:
        return peer
    for host in reversed(hosts):
        if not is_trusted(host, nets):
            return host
    # Every hop is a trusted proxy (misconfiguration, or the proxy itself
    # is calling): the leftmost is the best guess.
    return hosts[0]


class ForwardedHeadersMiddleware:
    """Pure ASGI: rewrite scope["client"] and scope["scheme"] from the
    forwarded headers, but only when the TCP peer is a trusted proxy. Takes the
    setting from stored_value, which answers from memory and refreshes in the
    background, so a change in Settings applies within seconds and without a
    restart and no request ever waits on the database here. Replaces uvicorn's
    --proxy-headers, which trusted everyone."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        client = scope.get("client")
        peer = client[0] if client else None
        nets = networks()
        trusted = bool(peer) and is_trusted(peer, nets)
        # Keep the TCP peer and the trust decision for the Settings page:
        # scope["client"] is about to be rewritten when the peer is trusted.
        scope.setdefault("state", {})
        scope["state"]["proxy_peer"] = peer
        scope["state"]["proxy_peer_trusted"] = trusted
        if peer and not trusted:
            if peer not in _warned_peers and len(_warned_peers) < _WARNED_MAX:
                for name, _ in scope["headers"]:
                    if name in (b"x-forwarded-for", b"x-forwarded-proto"):
                        _warned_peers.add(peer)
                        log.warning(
                            "ignoring forwarded headers from untrusted peer %s; if this is "
                            "the ingress, add it to trusted proxies (Settings, or "
                            "VCF_DOCTOR_TRUSTED_PROXIES) so client addresses and https "
                            "are recognised",
                            peer,
                        )
                        break
        if trusted:
            proto = None
            forwarded_for: list[str] = []
            for name, value in scope["headers"]:
                if name == b"x-forwarded-proto":
                    proto = value.decode("latin1").strip().lower()
                elif name == b"x-forwarded-for":
                    forwarded_for.append(value.decode("latin1"))
            if proto in ("http", "https", "ws", "wss"):
                if scope["type"] == "websocket":
                    scope["scheme"] = proto.replace("http", "ws")
                else:
                    scope["scheme"] = proto.replace("ws", "http")
            host = resolve_client(peer, forwarded_for, nets)
            if host and host != peer:
                scope["client"] = (host, 0)
        return await self.app(scope, receive, send)


# A forwarded entry that is not a valid address is still used as the key
# (it is what the trusted proxy reported), but never at header length.
_KEY_MAX = 64


def client_ip(request) -> str:
    """The address the login limiter keys on. Runs after the middleware, so
    request.client already reflects the trusted forwarded chain."""
    host = request.client.host if request.client else "unknown"
    return host[:_KEY_MAX]
