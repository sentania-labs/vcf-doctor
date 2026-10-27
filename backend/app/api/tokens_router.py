"""/api/tokens: a signed-in person's own API tokens.

Gated by the /api session middleware like everything else, and then by one more
rule: tokens are managed from a browser session that came from single sign-on.
The shared operator password is nobody in particular, so it has no tokens, and a
token cannot mint or revoke tokens (a leaked one must not be able to make
itself immortal). The secret appears in the response to the create call and
nowhere else.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app import auth, oidc, tokens

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class NewToken(BaseModel):
    name: str
    expires_in_days: int = Field(default=tokens.DEFAULT_DAYS, ge=1, le=tokens.MAX_DAYS)


class TokenInfo(BaseModel):
    id: str
    name: str
    hint: str
    role: str
    created_at: str
    expires_at: str
    last_used_at: str | None
    revoked_at: str | None
    revoked_reason: str | None


class TokenList(BaseModel):
    tokens: list[TokenInfo]
    max_days: int
    default_days: int
    max_active: int


class CreatedToken(BaseModel):
    token: str  # the secret, shown once
    info: TokenInfo


def _owner(request: Request) -> auth.Session:
    session = auth.session_for(request)
    if session is None:
        raise HTTPException(409, "API tokens need single sign-on; authentication is disabled")
    if not session.sso:
        raise HTTPException(
            403,
            "API tokens belong to a person: sign in through single sign-on to manage yours "
            "(the operator password and API tokens cannot)",
        )
    return session


def _info(row: dict) -> TokenInfo:
    return TokenInfo(**{k: row[k] for k in TokenInfo.model_fields})


@router.get("", response_model=TokenList)
def list_tokens(request: Request) -> TokenList:
    me = _owner(request)
    return TokenList(
        tokens=[_info(r) for r in tokens.list_for(me.issuer, me.subject)],
        max_days=tokens.MAX_DAYS,
        default_days=tokens.DEFAULT_DAYS,
        max_active=tokens.MAX_ACTIVE,
    )


@router.post("", response_model=CreatedToken, status_code=201)
def create_token(body: NewToken, request: Request) -> CreatedToken:
    me = _owner(request)
    name = oidc.clean_text(body.name, tokens.MAX_NAME)
    if not name:
        raise HTTPException(400, "give the token a name")
    try:
        record, secret = tokens.create(
            issuer=me.issuer,
            subject=me.subject,
            owner_name=me.name,
            role=me.role or oidc.ROLE_ADMIN,
            name=name,
            days=body.expires_in_days,
        )
    except tokens.TooManyTokens as exc:
        raise HTTPException(409, f"{exc}; revoke one first") from exc
    auth.audit.info(
        "API token %r (%s...) created by %s, expires %s",
        record["name"],
        record["hint"],
        me.describe(),
        record["expires_at"],
    )
    return CreatedToken(token=secret, info=_info(record))


@router.delete("/{token_id}")
def revoke_token(token_id: str, request: Request) -> dict:
    me = _owner(request)
    if not tokens.revoke(me.issuer, me.subject, token_id):
        raise HTTPException(404, "no such token")
    auth.audit.info("API token %s revoked by %s", token_id, me.describe())
    return {"ok": True}
