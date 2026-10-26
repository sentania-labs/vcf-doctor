"""GET/PUT /api/settings/oidc and POST /api/settings/oidc/test: the single
sign-on settings. Session-protected like every other settings endpoint (the
/api middleware in main.py handles that). The client secret is not here: it is
read from the file named by VCF_DOCTOR_OIDC_CLIENT_SECRET_FILE and only its
presence is reported."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import auth, oidc
from app.config import oidc_client_secret

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
    # Whether a client secret file is mounted (a public client has none).
    client_secret: bool
    # Authentication switched off by the deployment makes all of this moot.
    auth_enabled: bool


class OidcTest(BaseModel):
    ok: bool
    error: str | None = None
    issuer: str | None = None
    end_session: bool = False
    pkce: bool = True
    secret: bool = False


def _current(request: Request) -> OidcSettings:
    problems = oidc.problems()
    return OidcSettings(
        stored=OidcValues(**oidc.stored()),
        pinned=oidc.env_pinned(),
        effective=OidcValues(**oidc.effective_values()),
        defaults={"scopes": oidc.DEFAULT_SCOPES, "groups_claim": oidc.DEFAULT_GROUPS_CLAIM},
        configured=not problems,
        problems=problems,
        callback_url=str(request.url_for("oidc_callback")),
        client_secret=oidc_client_secret() is not None,
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


@router.post("/oidc/test", response_model=OidcTest)
def test_oidc() -> OidcTest:
    """Asks the provider for its discovery document, which is what every
    sign-in starts with."""
    cfg = oidc.effective()
    if cfg is None:
        raise HTTPException(400, "; ".join(oidc.problems()))
    try:
        return OidcTest(ok=True, **oidc.check(cfg))
    except oidc.SignInError as exc:
        return OidcTest(ok=False, error=exc.detail)
