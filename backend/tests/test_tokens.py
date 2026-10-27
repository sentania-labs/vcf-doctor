# ruff: noqa: F811, F401  (imported fixtures are used by name as test arguments)
"""Personal API tokens, tested through the mocked identity provider that
tests/test_oidc.py builds: the whole lifecycle, and what happens to a person's
tokens when they are refused at a later sign-in."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.test_oidc import (
    ADMIN_GROUP,
    ALICE,
    ISSUER,
    app_client,
    callback,
    idp,
    refused,
)

CONNECTION = {"name": "x", "host": "fixture", "username": "u", "password": "", "kind": "fixture"}
BOB = {"sub": "user-bob", "name": "Bob Builder", "email": "bob@example.test"}


def bearer(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"}


def make(client, name="ci", days=None) -> dict:
    body: dict = {"name": name}
    if days is not None:
        body["expires_in_days"] = days
    r = client.post("/api/tokens", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def script() -> TestClient:
    """A caller with no cookies, as a script would be."""
    from app.main import app

    return TestClient(app, follow_redirects=False)


@pytest.fixture
def alice(app_client, idp):
    assert callback(app_client, idp).status_code == 303
    assert app_client.get("/api/connections").status_code == 200
    return app_client


# ---- the lifecycle ----------------------------------------------------------


def test_a_person_creates_a_token_and_a_script_uses_it(alice):
    made = make(alice, "nightly export")
    secret = made["token"]
    assert secret.startswith("vcfd_pat_") and len(secret) > 40
    assert made["info"]["name"] == "nightly export"
    assert made["info"]["role"] == "admin"
    with script() as s:
        assert s.get("/api/connections").status_code == 401
        r = s.get("/api/connections", headers=bearer(secret))
        assert r.status_code == 200
        # The token is a bearer credential, not a cookie: no session is set.
        assert "vcfdoctor_session" not in r.cookies
        # Full access: the same writes a signed-in admin can make.
        created = s.post(
            "/api/connections",
            headers=bearer(secret),
            json=CONNECTION,
        )
        assert created.status_code == 201, created.text


def test_the_secret_is_shown_once_and_stored_only_as_a_hash(alice):
    from app import db, tokens

    secret = make(alice)["token"]
    listing = alice.get("/api/tokens")
    assert secret not in listing.text
    assert "token_hash" not in listing.text
    rows = db.fetchall("SELECT * FROM api_tokens")
    assert len(rows) == 1
    stored = " ".join(str(v) for v in rows[0].values())
    assert secret not in stored
    assert rows[0]["token_hash"] == tokens.hash_secret(secret)
    assert rows[0]["token_hash"] != secret
    # The hint shows enough to tell tokens apart and not enough to use one.
    assert rows[0]["hint"] == secret[: len("vcfd_pat_") + 4]


def test_a_person_lists_only_their_own_tokens(alice, idp):
    make(alice, "mine")
    alice.post("/api/auth/logout")
    alice.cookies.clear()
    assert callback(alice, idp, user=BOB, groups=[ADMIN_GROUP]).status_code == 303
    assert alice.get("/api/tokens").json()["tokens"] == []
    make(alice, "bobs")
    assert [t["name"] for t in alice.get("/api/tokens").json()["tokens"]] == ["bobs"]


def test_listing_reports_limits_and_use(alice):
    secret = make(alice)["token"]
    body = alice.get("/api/tokens").json()
    assert body["max_days"] == 365 and body["default_days"] == 30
    assert body["tokens"][0]["last_used_at"] is None
    with script() as s:
        s.get("/api/connections", headers=bearer(secret))
    assert alice.get("/api/tokens").json()["tokens"][0]["last_used_at"]


def test_revoking_a_token_stops_it_at_once(alice):
    made = make(alice)
    with script() as s:
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 200
        assert alice.delete(f"/api/tokens/{made['info']['id']}").status_code == 200
        r = s.get("/api/connections", headers=bearer(made["token"]))
        assert r.status_code == 401
    listed = alice.get("/api/tokens").json()["tokens"][0]
    assert listed["revoked_at"] and listed["revoked_reason"] == "revoked"
    # Revoking again is harmless and keeps the first revocation.
    assert alice.delete(f"/api/tokens/{made['info']['id']}").status_code == 200


def test_one_person_cannot_revoke_anothers_token(alice, idp):
    made = make(alice)
    alice.cookies.clear()
    assert callback(alice, idp, user=BOB, groups=[ADMIN_GROUP]).status_code == 303
    assert alice.delete(f"/api/tokens/{made['info']['id']}").status_code == 404
    with script() as s:
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 200


def test_tokens_expire(alice):
    from app import db

    made = make(alice, days=1)
    expires = datetime.fromisoformat(made["info"]["expires_at"])
    assert timedelta(hours=23) < expires - datetime.now(UTC) <= timedelta(days=1)
    with script() as s:
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 200
        past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="seconds")
        db.fetchall("UPDATE api_tokens SET expires_at = %s RETURNING id", (past,))
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 401


@pytest.mark.parametrize("days", [0, -1, 366, 10_000])
def test_the_lifetime_is_bounded(alice, days):
    assert alice.post("/api/tokens", json={"name": "x", "expires_in_days": days}).status_code == 422


def test_a_token_needs_a_name_and_the_name_is_made_safe(alice):
    assert alice.post("/api/tokens", json={"name": "   "}).status_code == 400
    made = make(alice, "line one\nline two\x00" + "x" * 300)
    name = made["info"]["name"]
    assert "\n" not in name and "\x00" not in name and len(name) <= 100


def test_there_is_a_cap_on_active_tokens(alice, monkeypatch):
    from app import tokens

    monkeypatch.setattr(tokens, "MAX_ACTIVE", 2)
    first = make(alice, "a")
    make(alice, "b")
    r = alice.post("/api/tokens", json={"name": "c"})
    assert r.status_code == 409 and "revoke one first" in r.json()["detail"]
    alice.delete(f"/api/tokens/{first['info']['id']}")
    make(alice, "c")


def test_old_spent_tokens_are_cleared_out(alice):
    from app import db

    made = make(alice, "old")
    alice.delete(f"/api/tokens/{made['info']['id']}")
    long_ago = (datetime.now(UTC) - timedelta(days=60)).isoformat(timespec="seconds")
    db.fetchall("UPDATE api_tokens SET revoked_at = %s RETURNING id", (long_ago,))
    make(alice, "new")
    assert [t["name"] for t in alice.get("/api/tokens").json()["tokens"]] == ["new"]


# ---- who may manage tokens --------------------------------------------------


def test_the_endpoints_need_a_session(app_client):
    assert app_client.get("/api/tokens").status_code == 401
    assert app_client.post("/api/tokens", json={"name": "x"}).status_code == 401
    assert app_client.delete("/api/tokens/abc").status_code == 401


def test_the_operator_password_has_no_tokens(app_client):
    app_client.post("/api/auth/login", json={"password": "break glass pw"})
    assert app_client.get("/api/connections").status_code == 200
    r = app_client.post("/api/tokens", json={"name": "x"})
    assert r.status_code == 403
    assert app_client.get("/api/tokens").status_code == 403


def test_a_token_cannot_manage_tokens(alice):
    made = make(alice)
    with script() as s:
        h = bearer(made["token"])
        assert s.post("/api/tokens", headers=h, json={"name": "more"}).status_code == 403
        assert s.get("/api/tokens", headers=h).status_code == 403
        assert s.delete(f"/api/tokens/{made['info']['id']}", headers=h).status_code == 403
    assert len(alice.get("/api/tokens").json()["tokens"]) == 1


def test_with_authentication_off_there_is_nothing_to_manage(app_client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth", "off")
    assert app_client.post("/api/tokens", json={"name": "x"}).status_code == 409


# ---- what a bearer header does ---------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "Bearer",
        "Bearer ",
        "Bearer nonsense",
        "Bearer vcfd_pat_" + "A" * 43,
        "Basic dXNlcjpwdw==",
        "Token vcfd_pat_x",
    ],
)
def test_bad_credentials_are_refused(alice, header):
    make(alice)
    with script() as s:
        assert s.get("/api/connections", headers={"Authorization": header}).status_code == 401


def test_a_dead_token_is_not_rescued_by_a_cookie(alice):
    made = make(alice)
    alice.delete(f"/api/tokens/{made['info']['id']}")
    assert alice.get("/api/connections").status_code == 200
    assert alice.get("/api/connections", headers=bearer(made["token"])).status_code == 401


def test_the_scheme_is_case_insensitive(alice):
    secret = make(alice)["token"]
    with script() as s:
        r = s.get("/api/connections", headers={"Authorization": f"bearer {secret}"})
        assert r.status_code == 200


def test_a_session_cookie_is_not_a_token(alice):
    cookie = alice.cookies.get("vcfdoctor_session")
    with script() as s:
        assert s.get("/api/connections", headers=bearer(cookie)).status_code == 401


def test_public_endpoints_ignore_tokens(app_client):
    with script() as s:
        assert s.get("/api/health", headers=bearer("junk")).status_code == 200
        assert s.get("/api/version", headers=bearer("junk")).status_code == 200


def test_auth_status_names_the_person_behind_a_token(alice):
    secret = make(alice)["token"]
    with script() as s:
        body = s.get("/api/auth/status", headers=bearer(secret)).json()
    assert body["authenticated"] is True
    assert body["user"]["name"] == "Alice Admin"


def test_changes_through_a_token_are_logged_as_the_person_and_the_token(alice, caplog):
    secret = make(alice, "nightly")["token"]
    with caplog.at_level(logging.INFO, logger="vcf_doctor.audit"):
        with script() as s:
            s.post(
                "/api/connections",
                headers=bearer(secret),
                json=CONNECTION,
            )
    line = next(m for m in caplog.messages if "POST /api/connections" in m)
    assert "API token 'nightly'" in line and "Alice Admin" in line and "sub=user-alice" in line
    assert secret not in caplog.text


def test_creating_and_revoking_are_logged_without_the_secret(alice, caplog):
    with caplog.at_level(logging.INFO, logger="vcf_doctor.audit"):
        made = make(alice, "nightly")
        alice.delete(f"/api/tokens/{made['info']['id']}")
    assert "created by 'Alice Admin'" in caplog.text
    assert f"API token {made['info']['id']} revoked" in caplog.text
    assert made["token"] not in caplog.text


# ---- the person's standing ---------------------------------------------------


def test_a_person_refused_at_a_later_sign_in_loses_their_tokens(alice, idp):
    first, second = make(alice, "one"), make(alice, "two")
    with script() as s:
        assert s.get("/api/connections", headers=bearer(first["token"])).status_code == 200
        alice.cookies.clear()
        # Alice was removed from the group at the provider.
        r = callback(alice, idp, groups=["everyone"])
        refused(alice, r, "not_authorized")
        for made in (first, second):
            assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 401
    from app import db

    rows = db.fetchall("SELECT revoked_at, revoked_reason FROM api_tokens")
    assert len(rows) == 2 and all(r["revoked_at"] for r in rows)
    assert all("refused at a later sign-in" in r["revoked_reason"] for r in rows)


def test_the_refusal_is_audited_with_the_count(alice, idp, caplog):
    make(alice)
    alice.cookies.clear()
    with caplog.at_level(logging.INFO, logger="vcf_doctor.audit"):
        callback(alice, idp, groups=[])
    assert "revoked 1 API token(s) of sub=user-alice" in caplog.text


def test_refusing_one_person_leaves_another_persons_tokens_alone(alice, idp):
    alices = make(alice, "alice's")
    alice.cookies.clear()
    assert callback(alice, idp, user=BOB, groups=[ADMIN_GROUP]).status_code == 303
    bobs = make(alice, "bob's")
    alice.cookies.clear()
    refused(alice, callback(alice, idp, user=BOB, groups=[]), "not_authorized")
    with script() as s:
        assert s.get("/api/connections", headers=bearer(bobs["token"])).status_code == 401
        assert s.get("/api/connections", headers=bearer(alices["token"])).status_code == 200


def test_a_person_with_no_groups_claim_loses_their_tokens_too(alice, idp):
    made = make(alice)
    alice.cookies.clear()
    refused(alice, callback(alice, idp, groups=None), "not_authorized")
    with script() as s:
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 401


@pytest.mark.parametrize(
    "patch",
    [{"aud": "someone-else"}, {"nonce": "forged"}, {"iss": "https://evil.test/"}],
)
def test_a_failed_verification_does_not_take_tokens_away(alice, idp, patch):
    """Only a verified person who is not in a group loses tokens. A forged or
    broken sign-in names nobody we can trust, so it must not be a way to knock
    someone's scripts offline."""
    made = make(alice)
    alice.cookies.clear()
    refused(alice, callback(alice, idp, groups=[], patch=patch), "failed")
    with script() as s:
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 200


def test_the_provider_being_down_does_not_take_tokens_away(alice, idp):
    made = make(alice)
    idp.up = False
    alice.cookies.clear()
    assert alice.get("/api/auth/oidc/login").headers["location"] == "/login?sso=unavailable"
    with script() as s:
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 200


def test_signing_in_again_keeps_the_tokens_and_refreshes_the_name(alice, idp):
    made = make(alice)
    alice.cookies.clear()
    assert callback(alice, idp, user={**ALICE, "name": "Alice Renamed"}).status_code == 303
    listed = alice.get("/api/tokens").json()["tokens"]
    assert [t["id"] for t in listed] == [made["info"]["id"]]
    with script() as s:
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 200


def test_a_token_carries_the_role_the_person_has_now(alice, idp):
    from app import db

    made = make(alice)
    # A stale role on the row: tokens are only honoured for roles that still exist.
    db.fetchall("UPDATE api_tokens SET role = 'gone' RETURNING id")
    with script() as s:
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 401
        alice.cookies.clear()
        assert callback(alice, idp).status_code == 303  # signing in restores the current role
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 200


def test_tokens_stop_when_single_sign_on_is_off_or_points_elsewhere(alice):
    from app import oidc

    made = make(alice)
    with script() as s:
        oidc.set_stored({"issuer": "https://other.test/", "client_id": "c", "admin_groups": ["g"]})
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 401
        oidc.set_stored({"issuer": ISSUER, "client_id": "c", "admin_groups": [ADMIN_GROUP]})
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 200
        oidc.set_stored({})
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 401


def test_changing_the_operator_password_leaves_tokens_alone(alice):
    """Tokens do not depend on the session signing secret, so the break-glass
    password change that ends every browser session does not end them. Revoke
    them, or refuse their owner, to cut them off."""
    made = make(alice)
    from app import auth

    auth.set_password("another password")
    with script() as s:
        assert s.get("/api/connections", headers=bearer(made["token"])).status_code == 200


# ---- with single sign-on unset ----------------------------------------------


def test_without_single_sign_on_a_token_header_changes_nothing(monkeypatch):
    from app import auth, db
    from app.config import settings
    from app.main import app

    db.reset_for_tests()
    monkeypatch.setattr(settings, "auth", "on")
    auth.set_initial_password("break glass pw")
    with TestClient(app, follow_redirects=False) as c:
        assert c.get("/api/connections", headers=bearer("vcfd_pat_x")).status_code == 401
        assert c.get("/api/connections").status_code == 401
        assert c.post("/api/auth/login", json={"password": "break glass pw"}).status_code == 200
        assert c.get("/api/connections").status_code == 200
        assert c.get("/api/tokens").status_code == 403
