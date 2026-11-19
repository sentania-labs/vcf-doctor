"""GET/PUT /api/settings/oidc: the single sign-on settings. Session-protected
like every other settings endpoint (the /api middleware in main.py handles
that). The client secret is write-only: PUT/DELETE /oidc/client-secret store
and remove it, encrypted like the assistant key, and only its presence, source
and change time are ever reported. A file named by
VCF_DOCTOR_OIDC_CLIENT_SECRET_FILE takes precedence over the stored one."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import auth, oidc

router = APIRouter(prefix="/api/settings", tags=["settings"])


class OidcValues(BaseModel):
    issuer: str = ""
    client_id: str = ""
    redirect_uri: str = ""
    scopes: str = ""
    groups_claim: str = ""
    admin_groups: list[str] = []


class OidcSettings(BaseModel):
    # What the Settings page saved; the form is filled from this.
    stored: OidcValues
    # Fields a VCF_DOCTOR_OIDC_* variable decides, and what it says.
    pinned: dict[str, bool]
    effective: OidcValues
    # Applied when a field is left empty.
    defaults: dict[str, str]
    # True when sign-in through the provider is available; otherwise the reasons.
    configured: bool
    problems: list[str]
    # What to register at the provider unless a redirect URI is set.
    callback_url: str
    # Whether a client secret is in use at all (a public client has none).
    client_secret: bool
    # Where it comes from: "deployment" (mounted file, wins), "stored" (saved
    # in Settings) or "none".
    client_secret_source: str
    # A secret is saved in Settings, whether or not a mounted file overrides it.
    client_secret_stored: bool
    client_secret_unreadable: bool
    client_secret_changed_at: str | None
    # Authentication switched off by the deployment makes all of this moot.
    auth_enabled: bool


def _current(request: Request) -> OidcSettings:
    problems = oidc.problems()
    secret, source = oidc.resolve_client_secret()
    return OidcSettings(
        stored=OidcValues(**oidc.stored()),
        pinned=oidc.env_pinned(),
        effective=OidcValues(**oidc.effective_values()),
        defaults={"scopes": oidc.DEFAULT_SCOPES, "groups_claim": oidc.DEFAULT_GROUPS_CLAIM},
        configured=not problems,
        problems=problems,
        callback_url=str(request.url_for("oidc_callback")),
        client_secret=secret is not None,
        client_secret_source=source,
        client_secret_stored=oidc.stored_secret_present(),
        client_secret_unreadable=oidc.stored_secret_unreadable(),
        client_secret_changed_at=oidc.secret_changed_at(),
        auth_enabled=auth.enabled(),
    )


@router.get("/oidc", response_model=OidcSettings)
def get_oidc(request: Request) -> OidcSettings:
    return _current(request)


@router.put("/oidc", response_model=OidcSettings)
def put_oidc(body: OidcValues, request: Request) -> OidcSettings:
    # A field a deployment variable decides is left as it was saved.
    saved = oidc.stored()
    pinned = oidc.env_pinned()
    submitted = body.model_dump()
    merged = {field: saved[field] if pinned[field] else submitted[field] for field in oidc.FIELDS}
    try:
        oidc.set_stored(merged)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    oidc.reset_cache()
    return _current(request)


@router.put("/oidc/client-secret", response_model=OidcSettings)
async def put_client_secret(request: Request) -> OidcSettings:
    """Save or replace the client secret. Never echoed, not even in a
    validation error (the body is read by hand so no framework error can quote
    it); the response is the ordinary settings view, which only says whether
    one is set."""
    try:
        payload = await request.json()
    except ValueError:
        payload = None
    secret = payload.get("secret") if isinstance(payload, dict) else None
    if not isinstance(secret, str):
        raise HTTPException(400, 'the body must be {"secret": "<text>"}')
    try:
        oidc.set_client_secret(secret)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _current(request)


@router.delete("/oidc/client-secret", response_model=OidcSettings)
def delete_client_secret(request: Request) -> OidcSettings:
    oidc.clear_client_secret()
    return _current(request)
