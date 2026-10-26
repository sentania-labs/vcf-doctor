"""Single sign-on, tested against a mocked identity provider.

The provider is a handler plugged into the HTTP client the app uses (an
httpx2 MockTransport), so nothing opens a socket. It signs ID tokens with a
local RSA key and checks what a real provider checks: the client, the redirect
URI, the PKCE verifier against the challenge it was given, and that an
authorization code is used once.
"""

import base64
import hashlib
import json
import logging
import time
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from authlib.integrations.httpx_client import OAuth2Client
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey

ISSUER = "https://idp.test/application/o/vcf-doctor/"
CLIENT_ID = "vcf-doctor-client"
CLIENT_SECRET = "s3cret-from-a-file"
ADMIN_GROUP = "team-admins"
KEY = RSAKey.generate_key(2048, parameters={"kid": "test-key"})
OTHER_KEY = RSAKey.generate_key(2048, parameters={"kid": "test-key"})

ALICE = {"sub": "user-alice", "name": "Alice Admin", "email": "alice@example.test"}
ALICE_GROUPS = [ADMIN_GROUP, "everyone"]


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class MockIdP:
    """Just enough of an OpenID Connect provider."""

    def __init__(self) -> None:
        self.up = True
        self.end_session = True
        self.signing_key = KEY
        self.codes: dict[str, dict] = {}
        self.token_requests: list[dict] = []
        self.discovery_requests = 0
        self.userinfo: dict | None = None
        self.last_subject = ""
        self.metadata_patch: dict = {}

    # -- the transport the app talks through --------------------------------

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if not self.up:
            raise httpx2.ConnectError("identity provider is down", request=request)
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            self.discovery_requests += 1
            return httpx2.Response(200, json=self.metadata())
        if path == "/jwks":
            return httpx2.Response(200, json={"keys": [KEY.as_dict(private=False)]})
        if path == "/token":
            return self.token(request)
        if path == "/userinfo":
            return httpx2.Response(200, json=self.userinfo or {"sub": self.last_subject})
        return httpx2.Response(404)

    def metadata(self) -> dict:
        meta = {
            "issuer": ISSUER,
            "authorization_endpoint": "https://idp.test/authorize",
            "token_endpoint": "https://idp.test/token",
            "jwks_uri": "https://idp.test/jwks",
            "userinfo_endpoint": "https://idp.test/userinfo",
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
        }
        if self.end_session:
            meta["end_session_endpoint"] = "https://idp.test/application/o/vcf-doctor/end-session/"
        return {**meta, **self.metadata_patch}

    # -- the authorization endpoint, as the person's browser would reach it --

    def authorize(self, location: str, user: dict, groups, patch: dict | None = None) -> str:
        """The person approves the request at `location`; returns the code the
        provider would send back."""
        url = urlsplit(location)
        assert f"{url.scheme}://{url.netloc}{url.path}" == "https://idp.test/authorize"
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        assert q["response_type"] == "code"
        assert q["client_id"] == CLIENT_ID
        assert q["code_challenge_method"] == "S256"
        assert "openid" in q["scope"].split()
        code = f"code-{len(self.codes) + 1}"
        self.codes[code] = {
            "user": user,
            "groups": groups,
            "nonce": q["nonce"],
            "challenge": q["code_challenge"],
            "redirect_uri": q["redirect_uri"],
            "patch": patch or {},
            "used": False,
        }
        return code

    # -- the token endpoint -------------------------------------------------

    def token(self, request: httpx2.Request) -> httpx2.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        record = {"form": form, "authorization": request.headers.get("authorization")}
        self.token_requests.append(record)
        rec = self.codes.get(form.get("code", ""))
        if rec is None or rec["used"]:
            return httpx2.Response(400, json={"error": "invalid_grant"})
        rec["used"] = True
        if form.get("redirect_uri") != rec["redirect_uri"]:
            return httpx2.Response(400, json={"error": "invalid_grant"})
        if _challenge(form.get("code_verifier", "")) != rec["challenge"]:
            return httpx2.Response(
                400, json={"error": "invalid_grant", "error_description": "pkce"}
            )
        self.last_subject = rec["user"]["sub"]
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "exp": now + 300,
            "iat": now,
            "nonce": rec["nonce"],
            **rec["user"],
        }
        if rec["groups"] is not None:
            claims["groups"] = rec["groups"]
        for key, value in rec["patch"].items():
            if value is None:
                claims.pop(key, None)
            else:
                claims[key] = value
        header = {"alg": "RS256", "kid": "test-key"}
        key = self.signing_key
        if rec["patch"].get("_alg") == "HS256":
            header, key = {"alg": "HS256"}, OctKey.import_key(CLIENT_SECRET * 4)
        claims.pop("_alg", None)
        id_token = jwt.encode(header, claims, key, algorithms=[header["alg"]])
        return httpx2.Response(
            200, json={"access_token": "at-1", "token_type": "Bearer", "id_token": id_token}
        )


@pytest.fixture
def idp(monkeypatch):
    provider = MockIdP()
    from app import oidc

    monkeypatch.setattr(
        oidc, "OAuth2Client", lambda **kw: OAuth2Client(transport=provider.transport(), **kw)
    )
    return provider


@pytest.fixture
def secret_file(tmp_path, monkeypatch):
    from app.config import settings

    path = tmp_path / "client-secret"
    path.write_text(CLIENT_SECRET + "\n")
    monkeypatch.setattr(settings, "oidc_client_secret_file", str(path))
    return path


@pytest.fixture
def app_client(monkeypatch, idp):
    """The app with authentication on, an operator password set and single
    sign-on configured against the mock provider."""
    from app import auth, db, oidc, proxies
    from app.config import settings
    from app.main import app

    db.reset_for_tests()
    proxies.reset_cache()
    oidc.reset_cache()
    auth.reset_login_state()
    monkeypatch.setattr(settings, "auth", "on")
    auth.set_initial_password("break glass pw")
    oidc.set_stored(
        {
            "issuer": ISSUER,
            "client_id": CLIENT_ID,
            "admin_groups": [ADMIN_GROUP],
        }
    )
    with TestClient(app, follow_redirects=False) as client:
        yield client
    auth.reset_login_state()


def start(client) -> str:
    r = client.get("/api/auth/oidc/login")
    assert r.status_code == 303, r.text
    return r.headers["location"]


def callback(client, idp, user=ALICE, groups=ALICE_GROUPS, patch=None, **query):
    """A whole sign-in: start, approve at the provider, come back."""
    location = start(client)
    code = idp.authorize(location, user, groups, patch)
    state = parse_qs(urlsplit(location).query)["state"][0]
    params = {"code": code, "state": state, **query}
    return client.get("/api/auth/oidc/callback", params=params)


def signed_in(client) -> bool:
    return client.get("/api/connections").status_code == 200


def refused(client, response, code):
    assert response.status_code == 303
    assert response.headers["location"] == f"/login?sso={code}"
    assert "vcfdoctor_session" not in response.cookies
    assert client.cookies.get("vcfdoctor_session") is None
    assert client.get("/api/connections").status_code == 401


# ---- signing in ------------------------------------------------------------


def test_member_of_an_admin_group_signs_in_with_full_access(app_client, idp, caplog):
    from app import auth

    with caplog.at_level(logging.INFO, logger="vcf_doctor.audit"):
        r = callback(app_client, idp)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert signed_in(app_client)
    # Everything the operator password can do, e.g. change settings.
    assert app_client.get("/api/settings/oidc").status_code == 200
    # The cookie says who it is: issuer plus subject, and a display name.
    session = auth.read_token(app_client.cookies.get(auth.COOKIE))
    assert (session.method, session.issuer, session.subject, session.name, session.role) == (
        "oidc",
        ISSUER,
        "user-alice",
        "Alice Admin",
        "admin",
    )
    status = app_client.get("/api/auth/status").json()
    assert status["authenticated"] is True
    assert status["oidc"] == {"enabled": True}
    assert status["user"] == {"name": "Alice Admin", "subject": "user-alice", "issuer": ISSUER}
    assert "signed in through single sign-on: 'Alice Admin' (sub=user-alice" in caplog.text


def test_authorization_request_carries_pkce_state_and_nonce(app_client, idp):
    q = parse_qs(urlsplit(start(app_client)).query)
    assert q["code_challenge_method"] == ["S256"]
    assert len(q["code_challenge"][0]) == 43
    assert len(q["state"][0]) >= 32 and len(q["nonce"][0]) >= 32
    assert q["scope"] == ["openid profile email"]
    assert q["redirect_uri"] == ["http://testserver/api/auth/oidc/callback"]
    # The attempt is remembered in a cookie scoped to the OIDC routes only.
    cookie = next(c for c in app_client.cookies.jar if c.name == "vcfdoctor_oidc")
    assert cookie.path == "/api/auth/oidc"
    # Two attempts never share state, nonce or challenge.
    q2 = parse_qs(urlsplit(start(app_client)).query)
    assert {q["state"][0], q["nonce"][0], q["code_challenge"][0]}.isdisjoint(
        {q2["state"][0], q2["nonce"][0], q2["code_challenge"][0]}
    )


def test_the_verifier_reaches_the_provider_and_the_secret_is_read_from_a_file(
    app_client, idp, secret_file
):
    assert callback(app_client, idp).headers["location"] == "/"
    request = idp.token_requests[0]
    assert request["form"]["grant_type"] == "authorization_code"
    assert len(request["form"]["code_verifier"]) >= 43
    # client_secret_basic by default: the secret is in the header, not the form.
    assert request["authorization"].startswith("Basic ")
    assert base64.b64decode(request["authorization"][6:]).decode() == f"{CLIENT_ID}:{CLIENT_SECRET}"
    assert "client_secret" not in request["form"]


def test_without_a_secret_file_the_client_is_public(app_client, idp, monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "oidc_client_secret_file", str(tmp_path / "missing"))
    assert callback(app_client, idp).headers["location"] == "/"
    request = idp.token_requests[0]
    assert request["authorization"] is None
    assert request["form"]["client_id"] == CLIENT_ID
    assert "client_secret" not in request["form"]


def test_secret_post_is_used_when_the_provider_only_offers_it(app_client, idp, secret_file):
    idp.metadata_patch = {"token_endpoint_auth_methods_supported": ["client_secret_post"]}
    assert callback(app_client, idp).headers["location"] == "/"
    request = idp.token_requests[0]
    assert request["authorization"] is None
    assert request["form"]["client_secret"] == CLIENT_SECRET


def test_group_names_are_not_in_the_code_or_the_defaults(app_client, idp):
    """Nothing configured means nobody gets in, whatever groups they carry."""
    from app import oidc

    oidc.set_stored({"issuer": ISSUER, "client_id": CLIENT_ID, "admin_groups": []})
    assert oidc.effective() is None
    assert any("admin group" in p for p in oidc.problems())
    r = app_client.get("/api/auth/oidc/login")
    assert r.headers["location"] == "/login?sso=not_configured"
    assert "oidc" not in app_client.get("/api/auth/status").json()


# ---- who is refused ---------------------------------------------------------


def test_user_outside_the_group_is_refused_and_gets_no_session(app_client, idp, caplog):
    with caplog.at_level(logging.INFO, logger="vcf_doctor.audit"):
        r = callback(app_client, idp, groups=["everyone", "other-team"])
    refused(app_client, r, "not_authorized")
    assert "user-alice" in caplog.text and "none of the admin groups" in caplog.text


def test_user_with_no_groups_claim_is_refused(app_client, idp):
    refused(app_client, callback(app_client, idp, groups=None), "not_authorized")


def test_group_names_must_match_exactly(app_client, idp):
    refused(app_client, callback(app_client, idp, groups=["TEAM-ADMINS"]), "not_authorized")
    app_client.cookies.clear()
    refused(app_client, callback(app_client, idp, groups=[ADMIN_GROUP + " "]), "not_authorized")


def test_groups_can_come_from_the_userinfo_endpoint(app_client, idp):
    idp.userinfo = {"sub": "user-alice", "groups": [ADMIN_GROUP]}
    assert callback(app_client, idp, groups=None).headers["location"] == "/"
    assert signed_in(app_client)


def test_userinfo_for_a_different_subject_is_not_trusted(app_client, idp):
    idp.userinfo = {"sub": "someone-else", "groups": [ADMIN_GROUP]}
    refused(app_client, callback(app_client, idp, groups=None), "failed")


def test_a_single_group_sent_as_a_string_is_accepted(app_client, idp):
    assert callback(app_client, idp, groups=ADMIN_GROUP).headers["location"] == "/"


def test_the_groups_claim_name_is_configurable(app_client, idp):
    from app import oidc

    oidc.set_stored(
        {
            "issuer": ISSUER,
            "client_id": CLIENT_ID,
            "admin_groups": ["ops"],
            "groups_claim": "https://example.test/roles",
        }
    )
    r = callback(app_client, idp, groups=None, patch={"https://example.test/roles": ["ops"]})
    assert r.headers["location"] == "/"
    app_client.cookies.clear()
    # The claim called "groups" no longer counts.
    refused(app_client, callback(app_client, idp, groups=["ops"]), "not_authorized")


def test_any_one_of_several_admin_groups_is_enough(app_client, idp):
    from app import oidc

    oidc.set_stored({"issuer": ISSUER, "client_id": CLIENT_ID, "admin_groups": ["a", "b"]})
    assert callback(app_client, idp, groups=["b"]).headers["location"] == "/"


def test_roles_are_rechecked_at_every_sign_in(app_client, idp):
    assert callback(app_client, idp).headers["location"] == "/"
    app_client.post("/api/auth/logout")
    refused(app_client, callback(app_client, idp, groups=["everyone"]), "not_authorized")


def test_provider_error_in_the_callback_is_a_refusal(app_client, idp):
    location = start(app_client)
    state = parse_qs(urlsplit(location).query)["state"][0]
    r = app_client.get("/api/auth/oidc/callback", params={"error": "access_denied", "state": state})
    refused(app_client, r, "denied")


# ---- tampering --------------------------------------------------------------


def test_wrong_state_is_refused(app_client, idp):
    refused(app_client, callback(app_client, idp, state="forged"), "expired")


def test_non_ascii_state_is_refused_as_expired(app_client, idp, caplog):
    with caplog.at_level(logging.ERROR):
        refused(app_client, callback(app_client, idp, state="\u00e9tat"), "expired")
    assert "Traceback" not in caplog.text
    assert idp.token_requests == []


def test_a_callback_without_a_started_sign_in_is_refused(app_client, idp):
    r = app_client.get("/api/auth/oidc/callback", params={"code": "x", "state": "y"})
    refused(app_client, r, "expired")
    assert idp.token_requests == []


def test_a_forged_or_foreign_flow_cookie_is_refused(app_client, idp):
    from app import auth

    app_client.cookies.set("vcfdoctor_oidc", "bm90LWEtcmVhbC1jb29raWU=", path="/api/auth/oidc")
    r = app_client.get("/api/auth/oidc/callback", params={"code": "x", "state": "y"})
    refused(app_client, r, "expired")
    # A session cookie is not a flow cookie, and a flow cookie is not a session.
    app_client.cookies.clear()
    app_client.post("/api/auth/login", json={"password": "break glass pw"})
    session_cookie = app_client.cookies.get(auth.COOKIE)
    assert auth.unseal_flow(session_cookie) is None
    flow = auth.seal_flow({"state": "s", "nonce": "n", "verifier": "v", "iat": int(time.time())})
    assert auth.read_token(flow) is None
    app_client.cookies.clear()
    app_client.cookies.set(auth.COOKIE, flow)
    assert app_client.get("/api/connections").status_code == 401


def test_an_attempt_that_took_too_long_is_refused(app_client, idp, monkeypatch):
    from app import oidc

    location = start(app_client)
    code = idp.authorize(location, ALICE, ALICE_GROUPS)
    state = parse_qs(urlsplit(location).query)["state"][0]
    later = time.time() + oidc.FLOW_TTL + 5
    monkeypatch.setattr(oidc.time, "time", lambda: later)
    r = app_client.get("/api/auth/oidc/callback", params={"code": code, "state": state})
    refused(app_client, r, "expired")


def test_a_code_cannot_be_replayed(app_client, idp):
    location = start(app_client)
    code = idp.authorize(location, ALICE, ALICE_GROUPS)
    state = parse_qs(urlsplit(location).query)["state"][0]
    flow_cookie = app_client.cookies.get("vcfdoctor_oidc", path="/api/auth/oidc")
    first = app_client.get("/api/auth/oidc/callback", params={"code": code, "state": state})
    assert first.headers["location"] == "/"
    # Someone replays the same callback with the same flow cookie.
    app_client.cookies.clear()
    app_client.cookies.set("vcfdoctor_oidc", flow_cookie, path="/api/auth/oidc")
    second = app_client.get("/api/auth/oidc/callback", params={"code": code, "state": state})
    refused(app_client, second, "failed")


@pytest.mark.parametrize(
    "patch",
    [
        {"nonce": "someone-elses-nonce"},
        {"nonce": None},
        {"aud": "another-client"},
        {"aud": ["another-client", "third"]},
        {"aud": [CLIENT_ID, "third"]},  # several audiences and no azp
        {"aud": [CLIENT_ID, "third"], "azp": "another-client"},
        {"iss": "https://evil.test/"},
        {"exp": int(time.time()) - 3600},
        {"exp": None},
        {"sub": None},
        {"sub": ""},
        {"iat": None},
    ],
    ids=lambda p: ",".join(f"{k}={v}" for k, v in p.items())[:60],
)
def test_a_bad_id_token_is_refused(app_client, idp, patch):
    refused(app_client, callback(app_client, idp, patch=patch), "failed")


def test_a_token_signed_by_another_key_is_refused(app_client, idp):
    idp.signing_key = OTHER_KEY
    refused(app_client, callback(app_client, idp), "failed")


def test_a_token_signed_with_the_client_secret_is_refused(app_client, idp, secret_file):
    """HS256 keyed with a secret the app also knows would let a holder of the
    client secret mint sessions; only asymmetric algorithms are accepted."""
    # Even a provider that advertises HS256 does not get it accepted.
    idp.metadata_patch = {"id_token_signing_alg_values_supported": ["HS256", "RS256"]}
    refused(app_client, callback(app_client, idp, patch={"_alg": "HS256"}), "failed")


def test_a_missing_id_token_is_refused(app_client, idp, monkeypatch):
    original = idp.token

    def without_id_token(request):
        response = original(request)
        body = json.loads(response.content)
        body.pop("id_token", None)
        return httpx2.Response(200, json=body)

    monkeypatch.setattr(idp, "token", without_id_token)
    refused(app_client, callback(app_client, idp), "failed")


def test_a_discovery_document_for_another_issuer_is_refused(app_client, idp):
    idp.metadata_patch = {"issuer": "https://evil.test/"}
    r = app_client.get("/api/auth/oidc/login")
    assert r.headers["location"] == "/login?sso=failed"


def test_display_names_are_made_safe(app_client, idp, caplog):
    nasty = {"sub": "u1", "name": "Mallory\nINFO forged line\x00" + "x" * 300}
    with caplog.at_level(logging.INFO, logger="vcf_doctor.audit"):
        assert callback(app_client, idp, user=nasty).headers["location"] == "/"
        app_client.post("/api/connections", json={})
    status = app_client.get("/api/auth/status").json()
    assert "\n" not in status["user"]["name"] and "\x00" not in status["user"]["name"]
    assert len(status["user"]["name"]) <= 100
    assert not any(line.startswith("INFO forged") for line in caplog.text.splitlines())


# ---- the identity provider is down -------------------------------------------


def test_with_the_provider_down_the_button_fails_readably_and_the_password_works(app_client, idp):
    idp.up = False
    r = app_client.get("/api/auth/oidc/login")
    assert r.status_code == 303
    assert r.headers["location"] == "/login?sso=unavailable"
    assert app_client.cookies.get("vcfdoctor_oidc") is None
    assert (
        app_client.post("/api/auth/login", json={"password": "break glass pw"}).status_code == 200
    )
    assert signed_in(app_client)


def test_a_provider_that_goes_down_mid_sign_in_is_reported(app_client, idp, monkeypatch):
    from app import oidc

    location = start(app_client)
    code = idp.authorize(location, ALICE, ALICE_GROUPS)
    state = parse_qs(urlsplit(location).query)["state"][0]
    idp.up = False
    oidc.reset_cache()
    r = app_client.get("/api/auth/oidc/callback", params={"code": code, "state": state})
    refused(app_client, r, "unavailable")


def test_the_provider_failing_the_token_request_is_reported(app_client, idp, monkeypatch):
    location = start(app_client)
    code = idp.authorize(location, ALICE, ALICE_GROUPS)
    state = parse_qs(urlsplit(location).query)["state"][0]

    def broken(request):
        raise httpx2.ReadTimeout("slow", request=request)

    monkeypatch.setattr(idp, "token", broken)
    r = app_client.get("/api/auth/oidc/callback", params={"code": code, "state": state})
    refused(app_client, r, "unavailable")


def test_a_provider_answering_garbage_is_not_a_500(app_client, idp, monkeypatch):
    monkeypatch.setattr(idp, "metadata", lambda: ["not", "an", "object"])
    assert app_client.get("/api/auth/oidc/login").headers["location"] == "/login?sso=unavailable"


def test_existing_sessions_keep_working_while_the_provider_is_down(app_client, idp):
    assert callback(app_client, idp).headers["location"] == "/"
    idp.up = False
    assert signed_in(app_client)
    status = app_client.get("/api/auth/status").json()
    assert status["authenticated"] is True and status["user"]["subject"] == "user-alice"


# ---- signing out --------------------------------------------------------------


def test_logout_clears_the_session_and_sends_the_browser_to_the_end_session_page(app_client, idp):
    callback(app_client, idp)
    r = app_client.post("/api/auth/logout")
    assert r.status_code == 200
    body = r.json()
    parts = urlsplit(body["redirect"])
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "https://idp.test/application/o/vcf-doctor/end-session/"
    )
    assert parse_qs(parts.query) == {"client_id": [CLIENT_ID]}
    assert body["ok"] is True
    assert app_client.get("/api/connections").status_code == 401


def test_logout_without_an_end_session_endpoint_just_clears_the_session(app_client, idp):
    idp.end_session = False
    callback(app_client, idp)
    assert app_client.post("/api/auth/logout").json() == {"ok": True}
    assert app_client.get("/api/connections").status_code == 401


def test_logout_works_with_the_provider_down(app_client, idp):
    from app import oidc

    callback(app_client, idp)
    idp.up = False
    oidc.reset_cache()
    assert app_client.post("/api/auth/logout").json() == {"ok": True}
    assert app_client.get("/api/connections").status_code == 401


def test_operator_logout_is_unchanged(app_client, idp):
    app_client.post("/api/auth/login", json={"password": "break glass pw"})
    assert app_client.post("/api/auth/logout").json() == {"ok": True}


# ---- the operator password stays --------------------------------------------


def test_the_operator_password_still_signs_in_and_is_logged_as_the_operator(
    app_client, idp, caplog
):
    from app import auth

    with caplog.at_level(logging.INFO, logger="vcf_doctor.audit"):
        assert (
            app_client.post("/api/auth/login", json={"password": "break glass pw"}).status_code
            == 200
        )
        app_client.put("/api/settings/oidc", json={"issuer": ISSUER, "client_id": CLIENT_ID})
    session = auth.read_token(app_client.cookies.get(auth.COOKIE))
    assert session.method == "password"
    assert "user" not in app_client.get("/api/auth/status").json()
    assert "operator password PUT /api/settings/oidc -> 200" in caplog.text


def test_changing_the_password_ends_single_sign_on_sessions_too(app_client, idp):
    callback(app_client, idp)
    cookie = app_client.cookies.get("vcfdoctor_session")
    app_client.post(
        "/api/auth/change",
        json={"current_password": "break glass pw", "new_password": "another pw 123"},
    )
    app_client.cookies.clear()
    app_client.cookies.set("vcfdoctor_session", cookie)
    assert app_client.get("/api/connections").status_code == 401


def test_turning_single_sign_on_off_ends_its_sessions(app_client, idp):
    from app import oidc

    callback(app_client, idp)
    assert signed_in(app_client)
    oidc.set_stored({})
    assert app_client.get("/api/connections").status_code == 401


def test_pointing_single_sign_on_at_another_provider_ends_its_sessions(app_client, idp):
    from app import oidc

    callback(app_client, idp)
    oidc.set_stored({"issuer": "https://other.test/", "client_id": "x", "admin_groups": ["g"]})
    assert app_client.get("/api/connections").status_code == 401


def test_an_issuer_written_with_or_without_its_trailing_slash_still_matches(app_client, idp):
    from app import oidc

    callback(app_client, idp)
    oidc.set_stored(
        {"issuer": ISSUER.rstrip("/"), "client_id": CLIENT_ID, "admin_groups": [ADMIN_GROUP]}
    )
    assert signed_in(app_client)


# ---- not configured ---------------------------------------------------------------


def test_without_single_sign_on_the_api_answers_exactly_what_it_always_did(
    app_client, idp, monkeypatch
):
    from app import oidc

    oidc.set_stored({})
    assert app_client.get("/api/auth/status").json() == {
        "enabled": True,
        "configured": True,
        "authenticated": False,
    }
    for path in ("/api/auth/oidc/login", "/api/auth/oidc/callback"):
        r = app_client.get(path, params={"code": "x", "state": "y"})
        assert r.status_code == 303 and r.headers["location"] == "/login?sso=not_configured"
        assert "vcfdoctor_session" not in r.headers.get("set-cookie", "")
    assert idp.discovery_requests == 0
    assert app_client.get("/api/connections").status_code == 401


def test_with_authentication_off_single_sign_on_does_nothing(app_client, idp, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth", "off")
    assert app_client.get("/api/auth/oidc/login").headers["location"] == "/login?sso=not_configured"
    status = app_client.get("/api/auth/status").json()
    assert status["enabled"] is False and "oidc" not in status


def test_public_endpoints_stay_public(app_client, idp):
    assert app_client.get("/api/health").status_code == 200
    assert app_client.get("/api/version").status_code == 200
    assert app_client.get("/api/connections").status_code == 401


# ---- the audit trail ---------------------------------------------------------------


def test_changes_are_logged_with_who_made_them(app_client, idp, caplog):
    callback(app_client, idp)
    with caplog.at_level(logging.INFO, logger="vcf_doctor.audit"):
        r = app_client.put("/api/settings/oidc", json={"issuer": ISSUER, "client_id": CLIENT_ID})
        assert r.status_code == 200
        app_client.get("/api/connections")
    lines = [r.getMessage() for r in caplog.records if r.name == "vcf_doctor.audit"]
    assert f"'Alice Admin' (sub=user-alice, iss={ISSUER}) PUT /api/settings/oidc -> 200" in lines
    # Reads are not audited.
    assert not any("GET" in line for line in lines)


def test_refusals_and_failures_are_logged(app_client, idp, caplog):
    with caplog.at_level(logging.INFO, logger="vcf_doctor.audit"):
        callback(app_client, idp, groups=[])
        app_client.post("/api/auth/login", json={"password": "wrong wrong"})
    text = caplog.text
    assert "single sign-on refused (not_authorized)" in text
    assert "operator password sign-in failed" in text


# ---- the settings ------------------------------------------------------------------


def test_the_settings_endpoints_need_a_session(app_client, idp):
    assert app_client.get("/api/settings/oidc").status_code == 401
    assert app_client.put("/api/settings/oidc", json={}).status_code == 401
    assert app_client.post("/api/settings/oidc/test").status_code == 401


def test_settings_round_trip_and_validation(app_client, idp):
    app_client.post("/api/auth/login", json={"password": "break glass pw"})
    body = {
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "redirect_uri": "https://doctor.example.test/api/auth/oidc/callback",
        "scopes": "profile groups",
        "groups_claim": "roles",
        "admin_groups": ["Domain Admins", "ops", "ops", " "],
    }
    r = app_client.put("/api/settings/oidc", json=body)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["stored"]["scopes"] == "openid profile groups"  # openid is always there
    assert got["stored"]["admin_groups"] == ["Domain Admins", "ops"]
    assert got["configured"] is True and got["problems"] == []
    assert got["defaults"] == {"scopes": "openid profile email", "groups_claim": "groups"}
    assert got["callback_url"] == "http://testserver/api/auth/oidc/callback"
    assert got["client_secret"] is False
    for bad in (
        {"issuer": "http://idp.example.test/"},
        {"issuer": "https://idp.example.test/?x=1"},
        {"issuer": "not a url"},
        {"redirect_uri": "https://doctor.example.test"},
        {"client_id": "has space"},
        {"admin_groups": ["x" * 300]},
    ):
        r = app_client.put("/api/settings/oidc", json={**body, **bad})
        assert r.status_code == 400, bad
    # Nothing bad was saved.
    assert app_client.get("/api/settings/oidc").json()["stored"]["issuer"] == ISSUER


def test_an_incomplete_configuration_says_what_is_missing(app_client, idp):
    app_client.post("/api/auth/login", json={"password": "break glass pw"})
    r = app_client.put("/api/settings/oidc", json={"issuer": ISSUER})
    assert r.status_code == 200
    got = r.json()
    assert got["configured"] is False
    assert got["problems"] == ["a client ID is required", "at least one admin group is required"]
    assert "oidc" not in app_client.get("/api/auth/status").json()


def test_deployment_variables_win_field_by_field(app_client, idp, monkeypatch):
    from app.config import settings

    app_client.post("/api/auth/login", json={"password": "break glass pw"})
    monkeypatch.setattr(settings, "oidc_admin_groups", "ops, Domain Admins")
    monkeypatch.setattr(settings, "oidc_client_id", "pinned-client")
    got = app_client.get("/api/settings/oidc").json()
    assert got["pinned"]["admin_groups"] is True and got["pinned"]["issuer"] is False
    assert got["effective"]["admin_groups"] == ["ops", "Domain Admins"]
    assert got["effective"]["client_id"] == "pinned-client"
    assert got["stored"]["client_id"] == CLIENT_ID
    # Saving from the page cannot change what the deployment decides.
    r = app_client.put(
        "/api/settings/oidc",
        json={"issuer": ISSUER, "client_id": "other", "admin_groups": ["nope"]},
    )
    assert r.json()["stored"]["client_id"] == CLIENT_ID
    assert r.json()["stored"]["admin_groups"] == [ADMIN_GROUP]
    # The pinned value is what a sign-in uses.
    app_client.cookies.clear()
    location = start(app_client)
    assert parse_qs(urlsplit(location).query)["client_id"] == ["pinned-client"]


def test_a_malformed_deployment_variable_fails_closed(app_client, idp, monkeypatch):
    from app import oidc
    from app.config import settings

    monkeypatch.setattr(settings, "oidc_issuer", "http://insecure.example.test/")
    assert oidc.effective() is None
    assert app_client.get("/api/auth/oidc/login").headers["location"] == "/login?sso=not_configured"


def test_the_test_button_reports_what_the_provider_offers(app_client, idp, secret_file):
    app_client.post("/api/auth/login", json={"password": "break glass pw"})
    r = app_client.post("/api/settings/oidc/test")
    assert r.json() == {
        "ok": True,
        "error": None,
        "issuer": ISSUER,
        "end_session": True,
        "pkce": True,
        "secret": True,
    }
    idp.up = False
    r = app_client.post("/api/settings/oidc/test")
    assert r.status_code == 200
    assert r.json()["ok"] is False and "down" in r.json()["error"]


def test_the_secret_never_appears_in_any_settings_response(app_client, idp, secret_file):
    app_client.post("/api/auth/login", json={"password": "break glass pw"})
    for response in (
        app_client.get("/api/settings/oidc"),
        app_client.post("/api/settings/oidc/test"),
        app_client.get("/api/auth/status"),
    ):
        assert CLIENT_SECRET not in response.text
