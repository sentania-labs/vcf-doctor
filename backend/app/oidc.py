"""Browser sign-in through any OpenID Connect provider.

Authorization code flow with PKCE (S256), state and nonce, against whatever
the issuer's discovery document describes. Nothing here knows one provider
from another. Authlib does the OAuth 2.0 client work (authorization URL, PKCE
challenge, token request, client authentication) and joserfc, its sibling
library, verifies the ID token.

Who may sign in is the deployer's decision and nothing in the code or the
defaults names a group. The groups claim is read from the ID token (or the
userinfo endpoint when the token omits it) and compared with the configured
admin groups; anyone who authenticates without one of them is refused and gets
no session. That check runs at every sign-in, so removing someone from a group
takes effect the next time they sign in (and at once for everybody when the
operator password is changed, which rotates the session signing secret).

Configuration lives in the settings table and is edited on the Settings page.
A VCF_DOCTOR_OIDC_* variable set on the deployment wins over the stored value,
field by field. The client secret is read from a file on every sign-in and is
never stored, shown or logged; no file means a public client that relies on
PKCE alone.

This module does not import app.auth: auth.py imports it to check that a
single-sign-on session still belongs to the configured provider.
"""

import logging
import secrets
import threading
import time
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx2
from authlib.common.security import generate_token
from authlib.integrations.base_client import OAuthError
from authlib.integrations.httpx_client import OAuth2Client
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError, JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from app import db
from app.config import oidc_client_secret, settings

log = logging.getLogger("vcf_doctor.oidc")

SETTING_KEY = "oidc"
ROLE_ADMIN = "admin"
DEFAULT_SCOPES = "openid profile email"
DEFAULT_GROUPS_CLAIM = "groups"

# The pieces of configuration an operator can set. Everything but the secret.
FIELDS = ("issuer", "client_id", "redirect_uri", "scopes", "groups_claim", "admin_groups")

HTTP_TIMEOUT = 5.0
# Discovery and signing keys change rarely; five minutes keeps sign-in off the
# provider's back without hiding a key rotation for long.
METADATA_TTL = 300.0
# How long a person has between leaving for the provider and coming back.
FLOW_TTL = 600
CLOCK_LEEWAY = 60
MAX_GROUPS = 64
MAX_NAME = 100
MAX_SUBJECT = 255  # OpenID Connect Core 2: "sub" is at most 255 ASCII characters
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
# Signatures from a shared secret would let anyone who knows the client secret
# mint a session, so only asymmetric algorithms are ever accepted.
ASYMMETRIC_ALGS = (
    "RS256", "RS384", "RS512", "PS256", "PS384", "PS512",
    "ES256", "ES384", "ES512", "EdDSA",
)  # fmt: skip

# What the login page shows is chosen by these codes; the detail only goes to
# the log, because it can describe the provider's internals.
UNAVAILABLE = "unavailable"
NOT_AUTHORIZED = "not_authorized"
DENIED = "denied"
EXPIRED = "expired"
FAILED = "failed"
NOT_CONFIGURED = "not_configured"


class SignInError(Exception):
    """A sign-in that did not complete. `code` is one of the constants above."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


@dataclass(frozen=True)
class Config:
    issuer: str
    client_id: str
    redirect_uri: str
    scopes: str
    groups_claim: str
    admin_groups: tuple[str, ...]


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    name: str
    role: str


# ---- configuration --------------------------------------------------------


def _is_http_url(
    value: str, *, label: str, needs_path: bool = False, allow_query: bool = False
) -> str:
    """The URL, if it is https (or http to this machine, for local development)
    with no fragment, and no query unless `allow_query` (a provider's own
    endpoints may carry one). Raises ValueError saying what is wrong."""
    value = value.strip()
    if len(value) > 500 or any(ord(c) < 33 for c in value):
        raise ValueError(f"{label} must be a URL without spaces")
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid URL") from exc
    if parts.scheme == "https":
        pass
    elif parts.scheme == "http" and host in LOOPBACK_HOSTS:
        pass
    else:
        raise ValueError(f"{label} must start with https://")
    if not host or parts.fragment or (parts.query and not allow_query):
        raise ValueError(f"{label} must be a plain URL with no query or fragment")
    if needs_path and parts.path in ("", "/"):
        raise ValueError(f"{label} must include a path")
    return value


def parse_issuer(raw: str) -> str:
    return _is_http_url(raw, label="the issuer")


def parse_redirect_uri(raw: str) -> str:
    return _is_http_url(raw, label="the redirect URI", needs_path=True)


def parse_client_id(raw: str) -> str:
    value = raw.strip()
    if len(value) > 255 or any(ord(c) < 33 for c in value):
        raise ValueError("the client ID must be at most 255 characters without spaces")
    return value


def parse_scopes(raw: str) -> str:
    """Space separated scopes; `openid` is always among them."""
    scopes: list[str] = []
    for scope in raw.replace(",", " ").split():
        if any(ord(c) < 33 or c in '"\\' for c in scope) or len(scope) > 100:
            raise ValueError(f"'{scope}' is not a valid scope")
        if scope not in scopes:
            scopes.append(scope)
    if not scopes:
        return ""
    if "openid" not in scopes:
        scopes.insert(0, "openid")
    return " ".join(scopes)


def parse_groups_claim(raw: str) -> str:
    value = raw.strip()
    if len(value) > 200 or any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("the groups claim name must be at most 200 characters")
    return value


def parse_groups(raw: Any) -> list[str]:
    """Group names from a list, or text with one per line or comma separated.
    Spaces are part of a name (directory groups have them)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        items: Iterable[Any] = raw.replace("\n", ",").split(",")
    elif isinstance(raw, list | tuple):
        items = raw
    else:
        raise ValueError("admin groups must be a list of group names")
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError("admin groups must be a list of group names")
        name = item.strip()
        if not name:
            continue
        if len(name) > 255 or any(unicodedata.category(c).startswith("C") for c in name):
            raise ValueError("a group name is at most 255 characters")
        if name not in out:
            out.append(name)
    if len(out) > MAX_GROUPS:
        raise ValueError(f"at most {MAX_GROUPS} admin groups")
    return out


_PARSERS = {
    "issuer": parse_issuer,
    "client_id": parse_client_id,
    "redirect_uri": parse_redirect_uri,
    "scopes": parse_scopes,
    "groups_claim": parse_groups_claim,
}


def _normalise(field: str, raw: Any) -> Any:
    """One configuration value in canonical form, or ValueError. Empty is
    always allowed: it means "not set"."""
    if field == "admin_groups":
        return parse_groups(raw)
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValueError(f"{field.replace('_', ' ')} must be text")
    if not raw.strip():
        return ""
    return _PARSERS[field](raw)


def stored() -> dict[str, Any]:
    """What the Settings page saved, tolerating a row that no longer parses."""
    raw = db.get_setting(SETTING_KEY) or {}
    out: dict[str, Any] = {}
    for field in FIELDS:
        try:
            out[field] = _normalise(field, raw.get(field))
        except ValueError:
            out[field] = [] if field == "admin_groups" else ""
    return out


def set_stored(values: Mapping[str, Any]) -> None:
    clean = {field: _normalise(field, values.get(field)) for field in FIELDS}
    db.set_setting(SETTING_KEY, clean)


def _env_raw(field: str) -> str:
    return (getattr(settings, f"oidc_{field}", "") or "").strip()


def env_pinned() -> dict[str, bool]:
    """Which fields a deployment variable currently decides."""
    return {field: bool(_env_raw(field)) for field in FIELDS}


def _effective_raw() -> dict[str, Any]:
    saved = stored()
    pinned = env_pinned()
    out: dict[str, Any] = {}
    for field in FIELDS:
        out[field] = _env_raw(field) if pinned[field] else saved[field]
    return out


def effective_values() -> dict[str, Any]:
    """The values in force as text, for display: a deployment variable wins
    field by field, and nothing is validated or defaulted."""
    values = _effective_raw()
    if isinstance(values["admin_groups"], str):
        values["admin_groups"] = [
            g.strip() for g in values["admin_groups"].replace("\n", ",").split(",") if g.strip()
        ]
    return values


def problems(values: Mapping[str, Any] | None = None) -> list[str]:
    """Why single sign-on is not usable, or [] when it is. A value that does
    not parse counts as missing, so a bad deployment variable fails closed."""
    values = _effective_raw() if values is None else values
    found: list[str] = []
    for field, label in (("issuer", "an issuer"), ("client_id", "a client ID")):
        try:
            ok = bool(_normalise(field, values.get(field)))
        except ValueError as exc:
            found.append(str(exc))
            continue
        if not ok:
            found.append(f"{label} is required")
    try:
        _normalise("redirect_uri", values.get("redirect_uri"))
        _normalise("scopes", values.get("scopes"))
        _normalise("groups_claim", values.get("groups_claim"))
        if not parse_groups(values.get("admin_groups")):
            found.append("at least one admin group is required")
    except ValueError as exc:
        found.append(str(exc))
    return found


def effective() -> Config | None:
    """The configuration in force, or None when single sign-on is off or
    incomplete."""
    values = _effective_raw()
    if problems(values):
        return None
    return Config(
        issuer=parse_issuer(values["issuer"]),
        client_id=parse_client_id(values["client_id"]),
        redirect_uri=_normalise("redirect_uri", values["redirect_uri"]),
        scopes=_normalise("scopes", values["scopes"]) or DEFAULT_SCOPES,
        groups_claim=_normalise("groups_claim", values["groups_claim"]) or DEFAULT_GROUPS_CLAIM,
        admin_groups=tuple(parse_groups(values["admin_groups"])),
    )


def configured() -> bool:
    return effective() is not None


def accepts_issuer(issuer: str | None) -> bool:
    """Whether a session signed in through `issuer` still belongs to the
    provider this deployment trusts. Turning single sign-on off, or pointing it
    at another provider, ends the sessions it had issued."""
    cfg = effective()
    return bool(cfg and issuer and _same_issuer(issuer, cfg.issuer))


def _same_issuer(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


# ---- talking to the provider ----------------------------------------------

_cache_lock = threading.Lock()
_metadata_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_keys_cache: dict[str, tuple[float, KeySet]] = {}


def reset_cache() -> None:
    with _cache_lock:
        _metadata_cache.clear()
        _keys_cache.clear()


def _client(cfg: Config, redirect_uri: str, auth_method: str = "none") -> OAuth2Client:
    secret = oidc_client_secret()
    return OAuth2Client(
        client_id=cfg.client_id,
        client_secret=secret,
        token_endpoint_auth_method=auth_method if secret else "none",
        scope=cfg.scopes,
        redirect_uri=redirect_uri,
        code_challenge_method="S256",
        timeout=HTTP_TIMEOUT,
        follow_redirects=False,
    )


def _get_json(client: OAuth2Client, url: str, **kwargs: Any) -> dict[str, Any]:
    try:
        resp = client.request("GET", url, withhold_token=True, **kwargs)
    except httpx2.HTTPError as exc:
        raise SignInError(UNAVAILABLE, f"{url}: {type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise SignInError(UNAVAILABLE, f"{url} answered HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise SignInError(UNAVAILABLE, f"{url} did not return JSON") from exc
    if not isinstance(body, dict):
        raise SignInError(UNAVAILABLE, f"{url} did not return a JSON object")
    return body


def _fetch_metadata(cfg: Config) -> dict[str, Any]:
    url = cfg.issuer.rstrip("/") + "/.well-known/openid-configuration"
    with _client(cfg, cfg.redirect_uri) as client:
        meta = _get_json(client, url)
    advertised = meta.get("issuer")
    if not isinstance(advertised, str) or not _same_issuer(advertised, cfg.issuer):
        raise SignInError(
            FAILED, f"discovery document names issuer {advertised!r}, not the configured one"
        )
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        try:
            _is_http_url(str(meta.get(key) or ""), label=key, allow_query=True)
        except ValueError as exc:
            raise SignInError(FAILED, f"discovery document: {exc}") from exc
    return meta


def discover(cfg: Config, *, fresh: bool = False) -> dict[str, Any]:
    """The provider's discovery document. `fresh` always asks the provider,
    which is how starting a sign-in notices a provider that is down; otherwise a
    recent copy is used, and a stale one stands in when the provider is
    unreachable so a blip does not break sessions mid-callback."""
    now = time.monotonic()
    with _cache_lock:
        cached = _metadata_cache.get(cfg.issuer)
    if cached and not fresh and now - cached[0] < METADATA_TTL:
        return cached[1]
    try:
        meta = _fetch_metadata(cfg)
    except SignInError as exc:
        if cached and not fresh and exc.code == UNAVAILABLE:
            log.warning("identity provider unreachable, using its cached metadata: %s", exc.detail)
            return cached[1]
        raise
    with _cache_lock:
        _metadata_cache[cfg.issuer] = (now, meta)
    return meta


def _keyset(cfg: Config, meta: dict[str, Any], *, refresh: bool) -> KeySet:
    with _cache_lock:
        cached = _keys_cache.get(cfg.issuer)
    if cached and not refresh and time.monotonic() - cached[0] < METADATA_TTL:
        return cached[1]
    with _client(cfg, cfg.redirect_uri) as client:
        document = _get_json(client, meta["jwks_uri"])
    try:
        keys = KeySet.import_key_set(document)
    except (JoseError, ValueError, KeyError, TypeError) as exc:
        raise SignInError(FAILED, f"signing keys could not be read: {exc}") from exc
    with _cache_lock:
        _keys_cache[cfg.issuer] = (time.monotonic(), keys)
    return keys


def _auth_method(meta: dict[str, Any]) -> str:
    supported = meta.get("token_endpoint_auth_methods_supported")
    if not isinstance(supported, list) or "client_secret_basic" in supported:
        return "client_secret_basic"
    if "client_secret_post" in supported:
        return "client_secret_post"
    return "client_secret_basic"


def check(cfg: Config) -> dict[str, Any]:
    """What the Settings page's test button reports: can the provider be
    reached and does it describe itself well enough to sign in against."""
    meta = discover(cfg, fresh=True)
    methods = meta.get("code_challenge_methods_supported")
    return {
        "issuer": meta.get("issuer"),
        "end_session": bool(meta.get("end_session_endpoint")),
        "pkce": not isinstance(methods, list) or "S256" in methods,
        "secret": oidc_client_secret() is not None,
    }


# ---- the flow -------------------------------------------------------------


def begin(cfg: Config, redirect_uri: str) -> tuple[str, dict[str, Any]]:
    """Where to send the browser, and what to remember until it comes back."""
    meta = discover(cfg, fresh=True)
    verifier = generate_token(64)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    with _client(cfg, redirect_uri, _auth_method(meta)) as client:
        url, _ = client.create_authorization_url(
            meta["authorization_endpoint"], state=state, code_verifier=verifier, nonce=nonce
        )
    flow = {
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "redirect_uri": redirect_uri,
        "issuer": cfg.issuer,
        "iat": int(time.time()),
    }
    return url, flow


def _clean(text: Any, limit: int) -> str:
    """Printable, single-line, bounded: it goes into a cookie and into logs."""
    if not isinstance(text, str):
        return ""
    single_line = " ".join(text.split())
    return "".join(c for c in single_line if not unicodedata.category(c).startswith("C"))[:limit]


def _groups(claims: Mapping[str, Any], claim: str) -> list[str] | None:
    """The groups in a claim: a list of names, or one name as a bare string.
    None when the claim is absent."""
    if claim not in claims:
        return None
    value = claims[claim]
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _verify_id_token(
    cfg: Config, meta: dict[str, Any], flow: Mapping[str, Any], token: str
) -> dict[str, Any]:
    advertised = meta.get("id_token_signing_alg_values_supported")
    allowed = [
        a
        for a in (advertised if isinstance(advertised, list) else ["RS256"])
        if a in ASYMMETRIC_ALGS
    ]
    if not allowed:
        raise SignInError(FAILED, "provider offers no asymmetric ID token signing algorithm")
    try:
        try:
            decoded = jwt.decode(token, _keyset(cfg, meta, refresh=False), algorithms=allowed)
        except InvalidKeyIdError:
            # The provider may have rotated its keys since we cached them.
            decoded = jwt.decode(token, _keyset(cfg, meta, refresh=True), algorithms=allowed)
        claims = decoded.claims
        JWTClaimsRegistry(
            leeway=CLOCK_LEEWAY,
            iss={"essential": True, "value": meta["issuer"]},
            aud={"essential": True, "value": cfg.client_id},
            sub={"essential": True},
            exp={"essential": True},
            iat={"essential": True},
            nonce={"essential": True, "value": flow["nonce"]},
        ).validate(claims)
    except JoseError as exc:
        raise SignInError(FAILED, f"ID token rejected: {exc}") from exc
    audience = claims["aud"]
    authorized_party = claims.get("azp")
    if authorized_party is not None and authorized_party != cfg.client_id:
        raise SignInError(FAILED, "ID token was issued to a different client (azp)")
    if isinstance(audience, list) and len(audience) > 1 and authorized_party is None:
        raise SignInError(FAILED, "ID token has several audiences and no azp")
    return claims


def complete(cfg: Config, flow: Mapping[str, Any], query: Mapping[str, str]) -> Identity:
    """Finish a sign-in: the provider sent the browser back with `query`, and
    `flow` is what begin() remembered. Raises SignInError for anything short of
    a verified person in an admin group."""
    if int(time.time()) - int(flow.get("iat", 0)) > FLOW_TTL:
        raise SignInError(EXPIRED, "the sign-in took too long")
    if not _same_issuer(str(flow.get("issuer", "")), cfg.issuer):
        raise SignInError(EXPIRED, "the provider changed during the sign-in")
    given_state = query.get("state", "")
    if not given_state or not secrets.compare_digest(
        given_state.encode(), str(flow["state"]).encode()
    ):
        raise SignInError(EXPIRED, "state does not match this browser's sign-in attempt")
    if query.get("error"):
        raise SignInError(
            DENIED, f"provider returned {query['error']}: {query.get('error_description', '')}"
        )
    code = query.get("code", "")
    if not code:
        raise SignInError(FAILED, "no authorization code in the response")

    meta = discover(cfg)
    try:
        with _client(cfg, flow["redirect_uri"], _auth_method(meta)) as client:
            token = client.fetch_token(
                meta["token_endpoint"],
                code=code,
                code_verifier=flow["verifier"],
                redirect_uri=flow["redirect_uri"],
                grant_type="authorization_code",
            )
            id_token = token.get("id_token")
            if not isinstance(id_token, str) or not id_token:
                raise SignInError(FAILED, "the token response has no ID token (openid scope?)")
            claims = _verify_id_token(cfg, meta, flow, id_token)
            groups = _groups(claims, cfg.groups_claim)
            if groups is None and meta.get("userinfo_endpoint") and token.get("access_token"):
                info = _get_json(
                    client,
                    meta["userinfo_endpoint"],
                    headers={"Authorization": f"Bearer {token['access_token']}"},
                )
                if info.get("sub") != claims["sub"]:
                    raise SignInError(FAILED, "userinfo describes a different subject")
                groups = _groups(info, cfg.groups_claim)
                claims = {**info, **claims}
    except OAuthError as exc:
        raise SignInError(FAILED, f"token request refused: {exc.error}: {exc.description}") from exc
    except httpx2.HTTPError as exc:
        raise SignInError(
            UNAVAILABLE, f"token request failed: {type(exc).__name__}: {exc}"
        ) from exc

    subject = claims["sub"]
    if not isinstance(subject, str) or not subject or len(subject) > MAX_SUBJECT:
        raise SignInError(FAILED, "ID token subject is not usable")
    issuer = str(claims["iss"])
    name = (
        _clean(claims.get("name"), MAX_NAME)
        or _clean(claims.get("preferred_username"), MAX_NAME)
        or _clean(claims.get("email"), MAX_NAME)
        or _clean(subject, MAX_NAME)
    )
    if not set(groups or ()) & set(cfg.admin_groups):
        raise SignInError(
            NOT_AUTHORIZED,
            f"{name!r} (sub={subject}, iss={issuer}) is in none of the admin groups"
            + ("" if groups is not None else f"; the provider sent no '{cfg.groups_claim}' claim"),
        )
    return Identity(issuer=issuer, subject=subject, name=name, role=ROLE_ADMIN)


def end_session_url(cfg: Config) -> str | None:
    """Where to send the browser to end the provider's own session, when the
    provider advertises such a place. Never raises: signing out must work with
    the provider down."""
    try:
        meta = discover(cfg)
    except SignInError:
        return None
    endpoint = meta.get("end_session_endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        return None
    try:
        _is_http_url(endpoint, label="end_session_endpoint", allow_query=True)
    except ValueError:
        return None
    joiner = "&" if urlsplit(endpoint).query else "?"
    return endpoint + joiner + urlencode({"client_id": cfg.client_id})
