# ruff: noqa: F811, F401  (imported fixtures are used by name as test arguments)
"""Service accounts and their tokens, through the mocked identity provider that
tests/test_oidc.py builds: the whole lifecycle (create, several live tokens for
rotation, revoke, disable and enable), who may manage them, and that the
personal tokens beside them are left alone."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.test_oidc import ADMIN_GROUP, app_client, callback, idp, refused
from tests.test_tokens import BOB, CONNECTION, bearer, make, script

PREFIX = "vcfd_sat_"


def account(client, name="ci-runner", **extra) -> dict:
    r = client.post("/api/service-accounts", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def issue(client, account_id, name="rotation-1", days=None) -> dict:
    body: dict = {"name": name}
    if days is not None:
        body["expires_in_days"] = days
    r = client.post(f"/api/service-accounts/{account_id}/tokens", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def works(secret: str) -> bool:
    with script() as s:
        r = s.get("/api/connections", headers=bearer(secret))
    assert r.status_code in (200, 401), r.text
    return r.status_code == 200


@pytest.fixture
def alice(app_client, idp):
    assert callback(app_client, idp).status_code == 303
    assert app_client.get("/api/connections").status_code == 200
    return app_client


@pytest.fixture
def operator(app_client):
    r = app_client.post("/api/auth/login", json={"password": "break glass pw"})
    assert r.status_code == 200, r.text
    return app_client


# ---- the lifecycle ----------------------------------------------------------


def test_an_admin_makes_an_account_and_a_script_uses_its_token(alice):
    made = account(alice, "ci-runner", description="nightly export job")
    assert made["role"] == "admin" and made["disabled_at"] is None
    assert made["description"] == "nightly export job"
    token = issue(alice, made["id"], "first")
    secret = token["token"]
    assert secret.startswith(PREFIX) and len(secret) > 40
    with script() as s:
        assert s.get("/api/connections").status_code == 401
        r = s.get("/api/connections", headers=bearer(secret))
        assert r.status_code == 200
        assert "vcfdoctor_session" not in r.cookies
        created = s.post("/api/connections", headers=bearer(secret), json=CONNECTION)
        assert created.status_code == 201, created.text
    assert alice.get("/api/service-accounts").json()["accounts"][0]["active_tokens"] == 1


def test_the_secret_is_shown_once_and_stored_only_as_a_hash(alice):
    from app import db, tokens

    secret = issue(alice, account(alice)["id"])["token"]
    listing = alice.get(f"/api/service-accounts/{account(alice, 'other')['id']}/tokens")
    assert secret not in listing.text
    rows = db.fetchall("SELECT * FROM api_tokens")
    assert len(rows) == 1
    assert secret not in " ".join(str(v) for v in rows[0].values())
    assert rows[0]["token_hash"] == tokens.hash_secret(secret)
    assert rows[0]["hint"] == secret[: len(PREFIX) + 4]
    assert rows[0]["issuer"] is None and rows[0]["subject"] is None


def test_an_account_holds_several_tokens_so_rotation_has_no_gap(alice):
    acct = account(alice)["id"]
    old = issue(alice, acct, "old")
    new = issue(alice, acct, "new")
    assert works(old["token"]) and works(new["token"])
    assert alice.get("/api/service-accounts").json()["accounts"][0]["active_tokens"] == 2
    revoked = alice.delete(f"/api/service-accounts/{acct}/tokens/{old['info']['id']}")
    assert revoked.status_code == 200
    assert not works(old["token"])
    assert works(new["token"])
    listed = {
        t["name"]: t for t in alice.get(f"/api/service-accounts/{acct}/tokens").json()["tokens"]
    }
    assert listed["old"]["revoked_at"] and listed["old"]["revoked_reason"] == "revoked"
    assert listed["new"]["revoked_at"] is None
    # Revoking again is harmless.
    again = alice.delete(f"/api/service-accounts/{acct}/tokens/{old['info']['id']}")
    assert again.status_code == 200


def test_there_is_no_limit_on_how_many_tokens_an_account_holds(alice):
    acct = account(alice)["id"]
    secrets = [issue(alice, acct, f"t{i}")["token"] for i in range(12)]
    assert all(works(s) for s in secrets[:1] + secrets[-1:])
    assert alice.get("/api/service-accounts").json()["accounts"][0]["active_tokens"] == 12


def test_disabling_an_account_stops_every_token_at_once(alice):
    acct = account(alice)["id"]
    first, second = issue(alice, acct, "a")["token"], issue(alice, acct, "b")["token"]
    assert works(first) and works(second)
    r = alice.post(f"/api/service-accounts/{acct}/disable")
    assert r.status_code == 200 and r.json()["disabled_at"]
    assert r.json()["active_tokens"] == 2  # not revoked, only held back
    assert not works(first) and not works(second)
    # Doing it twice keeps the first record.
    assert (
        alice.post(f"/api/service-accounts/{acct}/disable").json()["disabled_at"]
        == r.json()["disabled_at"]
    )
    # No new token while it is disabled.
    refused_new = alice.post(f"/api/service-accounts/{acct}/tokens", json={"name": "c"})
    assert refused_new.status_code == 409


def test_enabling_an_account_restores_only_tokens_that_were_never_revoked(alice):
    acct = account(alice)["id"]
    kept, revoked = issue(alice, acct, "kept"), issue(alice, acct, "revoked")
    alice.delete(f"/api/service-accounts/{acct}/tokens/{revoked['info']['id']}")
    alice.post(f"/api/service-accounts/{acct}/disable")
    assert not works(kept["token"])
    r = alice.post(f"/api/service-accounts/{acct}/enable")
    assert r.status_code == 200 and r.json()["disabled_at"] is None
    assert works(kept["token"]) and not works(revoked["token"])


def test_disabling_one_account_leaves_the_others_and_personal_tokens_alone(alice):
    one, two = account(alice, "one")["id"], account(alice, "two")["id"]
    a, b = issue(alice, one)["token"], issue(alice, two)["token"]
    personal = make(alice, "mine")["token"]
    alice.post(f"/api/service-accounts/{one}/disable")
    assert not works(a)
    assert works(b) and works(personal)


def test_tokens_expire(alice):
    from app import db

    acct = account(alice)["id"]
    made = issue(alice, acct, days=1)
    expires = datetime.fromisoformat(made["info"]["expires_at"])
    assert timedelta(hours=23) < expires - datetime.now(UTC) <= timedelta(days=1)
    assert works(made["token"])
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="seconds")
    db.fetchall("UPDATE api_tokens SET expires_at = %s RETURNING id", (past,))
    assert not works(made["token"])
    assert alice.get("/api/service-accounts").json()["accounts"][0]["active_tokens"] == 0


def test_a_lifetime_is_required_but_not_capped(alice):
    acct = account(alice)["id"]
    path = f"/api/service-accounts/{acct}/tokens"
    for days in (0, -1):
        assert alice.post(path, json={"name": "x", "expires_in_days": days}).status_code == 422
    long = issue(alice, acct, days=3650)  # ten years: past the personal-token limit
    assert datetime.fromisoformat(long["info"]["expires_at"]) - datetime.now(UTC) > timedelta(
        days=3600
    )


def test_names_are_made_safe_and_must_be_unique(alice):
    assert alice.post("/api/service-accounts", json={"name": "  "}).status_code == 400
    made = account(alice, "line one\nline two\x00" + "x" * 300)
    assert "\n" not in made["name"] and "\x00" not in made["name"] and len(made["name"]) <= 100
    clash = alice.post("/api/service-accounts", json={"name": made["name"]})
    assert clash.status_code == 409
    assert (
        alice.post(f"/api/service-accounts/{made['id']}/tokens", json={"name": " "}).status_code
        == 400
    )


def test_an_unknown_account_or_token_is_not_found(alice):
    acct = account(alice)["id"]
    other = account(alice, "other")["id"]
    token = issue(alice, other)
    assert alice.get("/api/service-accounts/nope/tokens").status_code == 404
    assert alice.post("/api/service-accounts/nope/tokens", json={"name": "x"}).status_code == 404
    assert alice.post("/api/service-accounts/nope/disable").status_code == 404
    assert alice.delete(f"/api/service-accounts/{acct}/tokens/nope").status_code == 404
    # A token is revoked through its own account only.
    wrong = alice.delete(f"/api/service-accounts/{acct}/tokens/{token['info']['id']}")
    assert wrong.status_code == 404
    assert works(token["token"])


# ---- who may manage them ----------------------------------------------------


def test_the_endpoints_need_a_session(app_client):
    assert app_client.get("/api/service-accounts").status_code == 401
    assert app_client.post("/api/service-accounts", json={"name": "x"}).status_code == 401
    assert app_client.post("/api/service-accounts/a/tokens", json={"name": "x"}).status_code == 401
    assert app_client.post("/api/service-accounts/a/disable").status_code == 401
    assert app_client.delete("/api/service-accounts/a/tokens/b").status_code == 401


def test_the_operator_password_is_an_administrator(operator):
    """Break-glass can manage service accounts, and it works without any
    single sign-on provider being involved."""
    acct = account(operator)
    secret = issue(operator, acct["id"])["token"]
    assert works(secret)
    assert operator.post(f"/api/service-accounts/{acct['id']}/disable").status_code == 200
    assert not works(secret)


def test_service_accounts_work_without_single_sign_on(monkeypatch):
    from app import auth, db, oidc, proxies
    from app.config import settings
    from app.main import app

    db.reset_for_tests()
    proxies.reset_cache()
    oidc.reset_cache()
    auth.reset_login_state()
    monkeypatch.setattr(settings, "auth", "on")
    auth.set_initial_password("break glass pw")
    with TestClient(app, follow_redirects=False) as client:
        client.post("/api/auth/login", json={"password": "break glass pw"})
        secret = issue(client, account(client)["id"])["token"]
        assert works(secret)
    auth.reset_login_state()


def test_a_token_of_any_kind_cannot_manage_service_accounts(alice):
    acct = account(alice)["id"]
    machine = issue(alice, acct)
    personal = make(alice)
    for secret in (machine["token"], personal["token"]):
        h = bearer(secret)
        with script() as s:
            assert s.get("/api/service-accounts", headers=h).status_code == 403
            assert s.post("/api/service-accounts", headers=h, json={"name": "x"}).status_code == 403
            assert (
                s.post(
                    f"/api/service-accounts/{acct}/tokens", headers=h, json={"name": "x"}
                ).status_code
                == 403
            )
            assert s.post(f"/api/service-accounts/{acct}/disable", headers=h).status_code == 403
            revoke = s.delete(
                f"/api/service-accounts/{acct}/tokens/{machine['info']['id']}", headers=h
            )
            assert revoke.status_code == 403
    assert len(alice.get(f"/api/service-accounts/{acct}/tokens").json()["tokens"]) == 1
    assert works(machine["token"])


def test_a_service_token_cannot_manage_personal_tokens(alice):
    secret = issue(alice, account(alice)["id"])["token"]
    with script() as s:
        h = bearer(secret)
        assert s.get("/api/tokens", headers=h).status_code == 403
        assert s.post("/api/tokens", headers=h, json={"name": "x"}).status_code == 403


def test_with_authentication_off_there_is_nothing_to_manage(app_client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth", "off")
    assert app_client.post("/api/service-accounts", json={"name": "x"}).status_code == 409


# ---- the role ---------------------------------------------------------------


def test_the_role_must_be_one_the_app_has(alice):
    bad = alice.post("/api/service-accounts", json={"name": "x", "role": "root"})
    assert bad.status_code == 400
    assert alice.get("/api/service-accounts").json()["roles"] == ["admin"]


def test_a_token_follows_its_accounts_current_role(alice):
    from app import db

    acct = account(alice)["id"]
    secret = issue(alice, acct)["token"]
    assert works(secret)
    db.fetchall("UPDATE service_accounts SET role = 'retired' RETURNING id")
    assert not works(secret)
    db.fetchall("UPDATE service_accounts SET role = 'admin' RETURNING id")
    assert works(secret)


# ---- a service account is nobody's, and is nobody's problem ----------------


def test_a_service_token_does_not_depend_on_a_person_or_a_provider(alice, idp):
    secret = issue(alice, account(alice)["id"])["token"]
    from app import oidc

    oidc.set_stored({})
    oidc.reset_cache()
    assert works(secret)


def test_refusing_a_person_leaves_service_tokens_working(alice, idp):
    secret = issue(alice, account(alice)["id"])["token"]
    alice.cookies.clear()
    refused(alice, callback(alice, idp, groups=["everyone"]), "not_authorized")
    assert works(secret)


def test_auth_status_for_a_service_token_names_no_person(alice):
    secret = issue(alice, account(alice)["id"])["token"]
    with script() as s:
        body = s.get("/api/auth/status", headers=bearer(secret)).json()
    assert body["authenticated"] is True and "user" not in body


# ---- what a bearer header does ---------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "Bearer vcfd_sat_",
        "Bearer vcfd_sat_nonsense",
        "Bearer vcfd_sat_" + "A" * 43,
        "Bearer vcfd_sat_" + "A" * 500,
    ],
)
def test_bad_credentials_are_refused(alice, header):
    issue(alice, account(alice)["id"])
    with script() as s:
        assert s.get("/api/connections", headers={"Authorization": header}).status_code == 401


def test_a_dead_service_token_is_not_rescued_by_a_cookie(alice):
    acct = account(alice)["id"]
    made = issue(alice, acct)
    alice.delete(f"/api/service-accounts/{acct}/tokens/{made['info']['id']}")
    assert alice.get("/api/connections").status_code == 200
    assert alice.get("/api/connections", headers=bearer(made["token"])).status_code == 401


def test_other_bearer_values_are_still_not_ours(alice):
    issue(alice, account(alice)["id"])
    with script() as s:
        for header in (
            "Bearer nonsense",
            "Bearer eyJhbGciOiJSUzI1NiJ9.e30.sig",
            "Token vcfd_sat_x",
        ):
            assert s.get("/api/connections", headers={"Authorization": header}).status_code == 401
    assert (
        alice.get("/api/connections", headers={"Authorization": "Bearer nonsense"}).status_code
        == 200
    )


# ---- the trail --------------------------------------------------------------


def test_changes_through_a_service_token_are_logged_as_the_account(alice, caplog):
    secret = issue(alice, account(alice, "ci-runner")["id"], "nightly")["token"]
    with caplog.at_level(logging.INFO, logger="vcf_doctor.audit"):
        with script() as s:
            s.post("/api/connections", headers=bearer(secret), json=CONNECTION)
    line = next(m for m in caplog.messages if "POST /api/connections" in m)
    assert "API token 'nightly'" in line and "service account 'ci-runner'" in line
    assert secret not in caplog.text


def test_management_is_logged_with_who_did_it_and_never_the_secret(alice, caplog):
    with caplog.at_level(logging.INFO, logger="vcf_doctor.audit"):
        acct = account(alice, "ci-runner")["id"]
        made = issue(alice, acct, "nightly")
        alice.post(f"/api/service-accounts/{acct}/disable")
        alice.post(f"/api/service-accounts/{acct}/enable")
        alice.delete(f"/api/service-accounts/{acct}/tokens/{made['info']['id']}")
    text = caplog.text
    assert "service account 'ci-runner'" in text
    for phrase in ("created by 'Alice Admin'", "disabled by", "enabled by", "revoked by"):
        assert phrase in text
    assert made["token"] not in text


# ---- personal tokens are untouched ------------------------------------------


def test_personal_tokens_and_service_tokens_do_not_see_each_other(alice, idp):
    mine = make(alice, "mine")
    acct = account(alice)["id"]
    issue(alice, acct, "machine")
    assert [t["name"] for t in alice.get("/api/tokens").json()["tokens"]] == ["mine"]
    assert [
        t["name"] for t in alice.get(f"/api/service-accounts/{acct}/tokens").json()["tokens"]
    ] == ["machine"]
    # A personal token is not revoked through the service account path, or the reverse.
    wrong = alice.delete(f"/api/service-accounts/{acct}/tokens/{mine['info']['id']}")
    assert wrong.status_code == 404
    assert works(mine["token"])
    # And the refusal of a person revokes theirs, not the machine's.
    alice.cookies.clear()
    refused(alice, callback(alice, idp, groups=["everyone"]), "not_authorized")
    assert not works(mine["token"])


def test_a_personal_token_secret_is_not_taken_for_a_service_one(alice):
    personal = make(alice)["token"]
    assert personal.startswith("vcfd_pat_")
    assert works(personal)
