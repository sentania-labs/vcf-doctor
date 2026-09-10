from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app import auth, proxies

router = APIRouter(prefix="/api/auth", tags=["auth"])


class PasswordBody(BaseModel):
    password: str


class ChangeBody(BaseModel):
    current_password: str
    new_password: str


def _set_cookie(response: Response, request: Request) -> None:
    response.set_cookie(
        auth.COOKIE,
        auth.issue_token(),
        max_age=auth.SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


@router.get("/status")
def status(request: Request) -> dict:
    return {
        "enabled": auth.enabled(),
        "configured": auth.configured(),
        "authenticated": auth.is_authenticated(request),
    }


@router.post("/setup")
def setup(body: PasswordBody, request: Request, response: Response) -> dict:
    if not auth.enabled():
        raise HTTPException(409, "authentication is disabled by the deployment")
    auth.bootstrap_from_env()
    if auth.configured():
        raise HTTPException(409, "password already set; use change")
    if not auth.set_initial_password(body.password):
        raise HTTPException(409, "password already set; use change")
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
def logout(response: Response) -> dict:
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.post("/change")
def change(body: ChangeBody, request: Request, response: Response):
    """Changing the password re-checks the current one, so it runs through the
    same per-client backoff as /login. A stolen session cannot use it to guess
    the current password any faster than the login page allows."""
    if not auth.is_authenticated(request):
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
    _set_cookie(response, request)
    return {"ok": True}
