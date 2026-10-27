import logging

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app import auth, oidc, proxies, tokens

log = logging.getLogger("vcf_doctor.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

# Carries state, nonce and the PKCE verifier from the redirect to the identity
# provider to the redirect back. Only the two OIDC routes ever see it.
FLOW_COOKIE = "vcfdoctor_oidc"
FLOW_COOKIE_PATH = "/api/auth/oidc"


class PasswordBody(BaseModel):
    password: str


class ChangeBody(BaseModel):
    current_password: str
    new_password: str


def _set_cookie(
    response: Response, request: Request, identity: oidc.Identity | None = None
) -> None:
    response.set_cookie(
        auth.COOKIE,
        auth.issue_token(identity),
        max_age=auth.SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


@router.get("/status")
def status(request: Request) -> dict:
    session = auth.session_for(request)
    body: dict = {
        "enabled": auth.enabled(),
        "configured": auth.configured(),
        "authenticated": session is not None or not auth.enabled(),
    }
    # Only present when single sign-on is set up, so a deployment without it
    # answers exactly what it always has.
    if auth.enabled() and oidc.configured():
        body["oidc"] = {"enabled": True}
    if session is not None and session.person:
        body["user"] = {"name": session.name, "subject": session.subject, "issuer": session.issuer}
    return body


@router.post("/setup")
def setup(body: PasswordBody, request: Request, response: Response) -> dict:
    if not auth.enabled():
        raise HTTPException(409, "authentication is disabled by the deployment")
    auth.bootstrap_from_env()
    if auth.configured():
        raise HTTPException(409, "password already set; use change")
    if not auth.set_initial_password(body.password):
        raise HTTPException(409, "password already set; use change")
    auth.audit.info("operator password set for the first time from %s", proxies.client_ip(request))
    _set_cookie(response, request)
    return {"ok": True}


@router.post("/login")
def login(body: PasswordBody, request: Request, response: Response):
    if not auth.configured():
        raise HTTPException(409, "no password set yet; use setup")
    ip = proxies.client_ip(request)
    wait, stamp = auth.begin_attempt(ip)
    if wait:
        return auth.too_many_response(wait)
    ok = auth.verify_password(body.password)
    auth.finish_attempt(ip, stamp, ok)
    auth.audit.log(
        logging.INFO if ok else logging.WARNING,
        "operator password sign-in %s from %s",
        "succeeded" if ok else "failed",
        ip,
    )
    if not ok:
        # Tell the caller now if the next attempt would be refused, so the
        # login page can start its countdown without a wasted request.
        wait = auth.login_blocked(ip)
        if wait:
            return auth.too_many_response(wait)
        raise HTTPException(401, "invalid password")
    _set_cookie(response, request)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    """Ends the local session. For someone who signed in through the identity
    provider, `redirect` names the provider's end-session page when it has one,
    so the browser can end that session too; with the provider unreachable the
    local session still ends."""
    session = auth.session_for(request)
    body: dict = {"ok": True}
    if session is not None:
        auth.audit.info("signed out: %s", session.describe())
    if session is not None and session.sso:
        cfg = oidc.effective()
        redirect = oidc.end_session_url(cfg) if cfg else None
        if redirect:
            body["redirect"] = redirect
    response.delete_cookie(auth.COOKIE, path="/")
    return body


def _login_page(code: str) -> RedirectResponse:
    """Back to the login page with a reason it can word. A browser navigation
    cannot show JSON, so every way a sign-in can fail ends here."""
    response = RedirectResponse(f"/login?sso={code}", status_code=303)
    response.delete_cookie(FLOW_COOKIE, path=FLOW_COOKIE_PATH)
    return response


def _cut_off(exc: oidc.SignInError) -> None:
    """The provider vouched for this person and they are no longer in an admin
    group: the sessions they already hold and the API tokens they made stop
    working now. Each step is tried on its own so one failing does not spare
    the other; a failure is logged and never becomes the sign-in's answer."""
    who = f"sub={exc.subject}, iss={exc.issuer}"
    try:
        auth.end_sessions(exc.issuer, exc.subject)
    except Exception:
        log.exception("could not end the sessions of %s", who)
    try:
        count = tokens.revoke_all(exc.issuer, exc.subject, tokens.REASON_REFUSED)
    except Exception:
        log.exception("could not revoke the API tokens of %s", who)
        return
    if count:
        auth.audit.warning("revoked %d API token(s) of %s: refused at sign-in", count, who)


@router.get("/oidc/login")
def oidc_login(request: Request) -> Response:
    """Starts a single sign-on: the browser is sent to the identity provider."""
    cfg = oidc.effective() if auth.enabled() else None
    if cfg is None:
        return _login_page(oidc.NOT_CONFIGURED)
    redirect_uri = cfg.redirect_uri or str(request.url_for("oidc_callback"))
    try:
        url, flow = oidc.begin(cfg, redirect_uri)
    except oidc.SignInError as exc:
        auth.audit.warning("single sign-on could not start: %s", exc.detail)
        return _login_page(exc.code)
    except Exception:
        log.exception("single sign-on could not start")
        return _login_page(oidc.FAILED)
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        FLOW_COOKIE,
        auth.seal_flow(flow),
        max_age=oidc.FLOW_TTL,
        httponly=True,
        samesite="lax",  # the provider's redirect back is a cross-site GET navigation
        secure=request.url.scheme == "https",
        path=FLOW_COOKIE_PATH,
    )
    return response


@router.get("/oidc/callback", name="oidc_callback")
def oidc_callback(request: Request) -> Response:
    """Where the identity provider sends the browser back. Public by necessity:
    the visitor has no session yet. It issues one only for a verified person in
    an admin group, and sends everyone else to the login page."""
    cfg = oidc.effective() if auth.enabled() else None
    if cfg is None:
        return _login_page(oidc.NOT_CONFIGURED)
    ip = proxies.client_ip(request)
    try:
        flow = auth.unseal_flow(request.cookies.get(FLOW_COOKIE))
        if flow is None:
            raise oidc.SignInError(oidc.EXPIRED, "no sign-in was started from this browser")
        identity = oidc.complete(cfg, flow, dict(request.query_params))
    except oidc.SignInError as exc:
        auth.audit.warning("single sign-on refused (%s) from %s: %s", exc.code, ip, exc.detail)
        if exc.code == oidc.NOT_AUTHORIZED and exc.subject:
            _cut_off(exc)
        return _login_page(exc.code)
    except Exception:
        log.exception("single sign-on failed unexpectedly")
        return _login_page(oidc.FAILED)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(FLOW_COOKIE, path=FLOW_COOKIE_PATH)
    _set_cookie(response, request, identity)
    auth.audit.info(
        "signed in through single sign-on: %r (sub=%s, iss=%s) as %s from %s",
        identity.name,
        identity.subject,
        identity.issuer,
        identity.role,
        ip,
    )
    return response


@router.post("/change")
def change(body: ChangeBody, request: Request, response: Response):
    """Changing the password re-checks the current one, so it runs through the
    same per-client backoff as /login. A stolen session cannot use it to guess
    the current password any faster than the login page allows."""
    session = auth.session_for(request)
    if session is None and auth.enabled():
        raise HTTPException(401, "authentication required")
    ip = proxies.client_ip(request)
    wait, stamp = auth.begin_attempt(ip)
    if wait:
        return auth.too_many_response(wait)
    ok = auth.verify_password(body.current_password)
    auth.finish_attempt(ip, stamp, ok)
    if not ok:
        wait = auth.login_blocked(ip)
        if wait:
            return auth.too_many_response(wait)
        raise HTTPException(401, "current password is incorrect")
    auth.set_password(body.new_password)
    auth.audit.info(
        "operator password changed by %s from %s",
        session.describe() if session else "operator password",
        ip,
    )
    _set_cookie(response, request)
    return {"ok": True}
