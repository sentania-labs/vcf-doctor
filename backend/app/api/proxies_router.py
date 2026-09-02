"""GET/PUT /api/settings/trusted-proxies: which addresses may set
X-Forwarded-For and X-Forwarded-Proto. Session-protected like every other
settings endpoint (the /api middleware in main.py handles that)."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import proxies

router = APIRouter(prefix="/api/settings", tags=["settings"])


class TrustedProxies(BaseModel):
    # Canonical networks in effect (env override or stored value).
    trusted_proxies: list[str]
    # "env" when VCF_DOCTOR_TRUSTED_PROXIES pins the list, else "settings".
    source: str
    # What the Settings page saved; shown greyed out while env wins.
    stored: list[str]
    # Set when the environment value could not be parsed (trust is then empty).
    env_problem: str | None = None
    # Where this very request came from, so the page can point at the
    # ingress: the TCP peer when it is not trusted, else the forwarded client.
    peer: str | None = None
    peer_trusted: bool = False
    # This request carried X-Forwarded-* headers that were ignored because
    # the peer is not trusted: almost certainly an ingress that should be listed.
    ignored_forwarded_headers: bool = False
    # What the app believes the scheme is (drives HSTS and the Secure cookie flag).
    scheme: str = "http"


class TrustedProxiesUpdate(BaseModel):
    trusted_proxies: list[str]


def _current(request: Request) -> TrustedProxies:
    value, source = proxies.effective()
    peer = request.client.host if request.client else None
    trusted = proxies.is_trusted(peer, proxies.networks())
    return TrustedProxies(
        trusted_proxies=value,
        source=source,
        stored=proxies.stored_value(),
        env_problem=proxies.env_problem(),
        peer=peer,
        peer_trusted=trusted,
        ignored_forwarded_headers=not trusted and proxies.forwarded_headers_present(request),
        scheme=request.url.scheme,
    )


@router.get("/trusted-proxies", response_model=TrustedProxies)
def get_trusted_proxies(request: Request) -> TrustedProxies:
    return _current(request)


@router.put("/trusted-proxies", response_model=TrustedProxies)
def put_trusted_proxies(body: TrustedProxiesUpdate, request: Request) -> TrustedProxies:
    if len(body.trusted_proxies) > 64:
        raise HTTPException(400, "at most 64 trusted proxy entries")
    try:
        proxies.set_stored(body.trusted_proxies)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _current(request)
