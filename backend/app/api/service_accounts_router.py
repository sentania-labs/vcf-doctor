"""/api/service-accounts: machine identities and their tokens.

Gated by the /api session middleware, and then by one more rule: an
administrator manages them from a browser session (single sign-on, or the
operator password, which is an administrator). An API token of any kind cannot,
so a leaked one cannot mint itself a successor or widen what it can reach. A
token's secret appears in the response to the create call and nowhere else.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app import auth, oidc, service_accounts, tokens
from app.api.tokens_router import TokenInfo, _info

router = APIRouter(prefix="/api/service-accounts", tags=["service-accounts"])

ROLES = (oidc.ROLE_ADMIN,)
# Not a policy: a day count much larger than this overflows the date it is added to.
MAX_DAYS = 36_500


class NewAccount(BaseModel):
    name: str
    description: str = ""
    role: str = oidc.ROLE_ADMIN


class AccountInfo(BaseModel):
    id: str
    name: str
    description: str
    role: str
    created_at: str
    created_by: str
    disabled_at: str | None
    disabled_by: str | None
    active_tokens: int = 0


class AccountList(BaseModel):
    accounts: list[AccountInfo]
    roles: list[str]
    default_days: int


class NewToken(BaseModel):
    name: str
    expires_in_days: int = Field(default=tokens.DEFAULT_DAYS, ge=1, le=MAX_DAYS)


class TokenList(BaseModel):
    tokens: list[TokenInfo]
    default_days: int


class CreatedToken(BaseModel):
    token: str  # the secret, shown once
    info: TokenInfo


def _admin(request: Request) -> auth.Session:
    session = auth.session_for(request)
    if session is None:
        raise HTTPException(409, "service accounts need authentication; it is disabled")
    if session.machine:
        raise HTTPException(
            403,
            "service accounts are managed from a browser session; an API token cannot",
        )
    if session.sso and session.role != oidc.ROLE_ADMIN:
        raise HTTPException(403, "administrators only")
    return session


def _account(account_id: str) -> dict:
    account = service_accounts.get(account_id)
    if account is None:
        raise HTTPException(404, "no such service account")
    return account


@router.get("", response_model=AccountList)
def list_accounts(request: Request) -> AccountList:
    _admin(request)
    return AccountList(
        accounts=[AccountInfo(**a) for a in service_accounts.listing()],
        roles=list(ROLES),
        default_days=tokens.DEFAULT_DAYS,
    )


@router.post("", response_model=AccountInfo, status_code=201)
def create_account(body: NewAccount, request: Request) -> AccountInfo:
    me = _admin(request)
    name = oidc.clean_text(body.name, service_accounts.MAX_NAME)
    if not name:
        raise HTTPException(400, "give the service account a name")
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of: {', '.join(ROLES)}")
    try:
        account = service_accounts.create(
            name=name,
            description=oidc.clean_text(body.description, service_accounts.MAX_DESCRIPTION),
            role=body.role,
            created_by=me.describe(),
        )
    except service_accounts.NameTaken:
        raise HTTPException(409, "a service account with that name already exists") from None
    auth.audit.info(
        "service account %r (%s) created by %s with role %s",
        account["name"],
        account["id"],
        me.describe(),
        account["role"],
    )
    return AccountInfo(**account)


def _set_disabled(account_id: str, disabled: bool, request: Request) -> AccountInfo:
    me = _admin(request)
    _account(account_id)
    account = service_accounts.set_disabled(account_id, disabled, me.describe())
    assert account is not None
    auth.audit.info(
        "service account %r (%s) %s by %s",
        account["name"],
        account["id"],
        "disabled" if disabled else "enabled",
        me.describe(),
    )
    return AccountInfo(**account)


@router.post("/{account_id}/disable", response_model=AccountInfo)
def disable_account(account_id: str, request: Request) -> AccountInfo:
    return _set_disabled(account_id, True, request)


@router.post("/{account_id}/enable", response_model=AccountInfo)
def enable_account(account_id: str, request: Request) -> AccountInfo:
    return _set_disabled(account_id, False, request)


@router.get("/{account_id}/tokens", response_model=TokenList)
def list_tokens(account_id: str, request: Request) -> TokenList:
    _admin(request)
    _account(account_id)
    return TokenList(
        tokens=[_info(r) for r in tokens.list_for_account(account_id)],
        default_days=tokens.DEFAULT_DAYS,
    )


@router.post("/{account_id}/tokens", response_model=CreatedToken, status_code=201)
def create_token(account_id: str, body: NewToken, request: Request) -> CreatedToken:
    me = _admin(request)
    account = _account(account_id)
    if account["disabled_at"]:
        raise HTTPException(409, "this service account is disabled; enable it first")
    name = oidc.clean_text(body.name, tokens.MAX_NAME)
    if not name:
        raise HTTPException(400, "give the token a name")
    record, secret = tokens.create(
        service_account_id=account_id,
        role=account["role"],
        name=name,
        days=body.expires_in_days,
    )
    auth.audit.info(
        "API token %r (%s...) of service account %r created by %s, expires %s",
        record["name"],
        record["hint"],
        account["name"],
        me.describe(),
        record["expires_at"],
    )
    return CreatedToken(token=secret, info=_info(record))


@router.delete("/{account_id}/tokens/{token_id}")
def revoke_token(account_id: str, token_id: str, request: Request) -> dict:
    me = _admin(request)
    account = _account(account_id)
    revoked_id = tokens.revoke_for_account(account_id, token_id)
    if revoked_id is None:
        raise HTTPException(404, "no such token")
    auth.audit.info(
        "API token %s of service account %r revoked by %s",
        revoked_id,
        account["name"],
        me.describe(),
    )
    return {"ok": True}
