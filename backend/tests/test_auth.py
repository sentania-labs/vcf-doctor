from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch, **env):
    from app import auth, db
    from app.config import settings

    db.reset_for_tests()
    monkeypatch.setattr(settings, "auth", "on")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import importlib

    import app.main as main

    importlib.reload(main)
    auth.bootstrap_from_env()
    return TestClient(main.app)


def test_first_run_setup_login_logout_change(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/version").status_code == 200
        assert c.get("/api/connections").status_code == 401
        st = c.get("/api/auth/status").json()
        assert st == {"enabled": True, "configured": False, "authenticated": False}
        assert c.post("/api/auth/setup", json={"password": "short"}).status_code == 400
        assert c.post("/api/auth/setup", json={"password": "correct horse"}).status_code == 200
        assert c.get("/api/connections").status_code == 200
        assert c.post("/api/auth/setup", json={"password": "again again"}).status_code == 409
        assert c.post("/api/auth/logout").status_code == 200
        assert c.get("/api/connections").status_code == 401
        assert c.post("/api/auth/login", json={"password": "wrong wrong"}).status_code == 401
        assert c.post("/api/auth/login", json={"password": "correct horse"}).status_code == 200
        r = c.post(
            "/api/auth/change",
            json={"current_password": "nope nope", "new_password": "new password"},
        )
        assert r.status_code == 401
        r = c.post(
            "/api/auth/change",
            json={"current_password": "correct horse", "new_password": "new password"},
        )
        assert r.status_code == 200
        c.post("/api/auth/logout")
        assert c.post("/api/auth/login", json={"password": "new password"}).status_code == 200


def test_concurrent_first_run_setup_has_one_winner(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import app.main as main
    from app import auth

    first_checks = threading.Barrier(2)
    second_checks = threading.Barrier(2)
    local = threading.local()
    configured = auth.configured

    def synchronized_configured():
        result = configured()
        count = getattr(local, "configured_checks", 0) + 1
        local.configured_checks = count
        if count == 1:
            first_checks.wait(timeout=2)
        elif count == 2:
            second_checks.wait(timeout=2)
        return result

    class Unlocked:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with _client(tmp_path, monkeypatch) as first, TestClient(main.app) as second:
        monkeypatch.setattr(auth, "configured", synchronized_configured)
        monkeypatch.setattr(auth, "setup_lock", Unlocked(), raising=False)
        passwords = ("first password", "second password")
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda pair: (
                        pair[0],
                        pair[1].post("/api/auth/setup", json={"password": pair[0]}),
                    ),
                    zip(passwords, (first, second), strict=True),
                )
            )

    assert sorted(response.status_code for _, response in responses) == [200, 409]
    accepted, winner = next(pair for pair in responses if pair[1].status_code == 200)
    rejected = next(password for password, response in responses if response.status_code == 409)
    assert auth.verify_password(accepted) is True
    assert auth.verify_password(rejected) is False
    assert auth.token_valid(winner.cookies.get(auth.COOKIE)) is True


def test_forged_cookie_is_rejected(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        c.cookies.set("vcfdoctor_session", "MTIzNDU2Nzg5MC5ib2d1cw==")
        assert c.get("/api/connections").status_code == 401


def test_env_seed_and_auth_off(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, VCF_DOCTOR_ADMIN_PASSWORD="seeded password") as c:
        assert c.get("/api/auth/status").json()["configured"] is True
        assert c.post("/api/auth/login", json={"password": "seeded password"}).status_code == 200
    from app.config import settings

    monkeypatch.setattr(settings, "auth", "off")
    with _client(tmp_path, monkeypatch) as c:
        from app.config import settings as s2

        monkeypatch.setattr(s2, "auth", "off")
        assert c.get("/api/auth/status").json()["enabled"] is False
        assert c.get("/api/connections").status_code == 200


def test_every_issued_token_validates(tmp_path, monkeypatch):
    """Regression: raw HMAC bytes containing 0x2E used to break delimiter parsing."""
    from app import auth, db

    db.reset_for_tests()
    for _ in range(300):
        assert auth.token_valid(auth.issue_token())


def test_password_change_invalidates_old_sessions(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        old = c.cookies.get("vcfdoctor_session")
        c.post(
            "/api/auth/change",
            json={"current_password": "correct horse", "new_password": "battery staple"},
        )
        c.cookies.set("vcfdoctor_session", old)
        assert c.get("/api/connections").status_code == 401


def test_login_backoff_after_repeated_failures(tmp_path, monkeypatch):
    from app import auth

    auth.reset_login_state()
    with _client(tmp_path, monkeypatch) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        c.post("/api/auth/logout")
        for _ in range(4):
            assert c.post("/api/auth/login", json={"password": "wrong wrong"}).status_code == 401
        # The fifth failure already answers 429 so the page can start counting down.
        r = c.post("/api/auth/login", json={"password": "wrong wrong"})
        assert r.status_code == 429 and r.headers["Retry-After"] == str(r.json()["retry_after"])
        r = c.post("/api/auth/login", json={"password": "correct horse"})
        assert r.status_code == 429 and "Retry-After" in r.headers
        assert r.json()["retry_after"] >= 1
    auth.reset_login_state()


def test_docs_are_not_public(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        for p in ("/docs", "/redoc", "/openapi.json"):
            assert "openapi" not in c.get(p).text.lower()


def test_password_change_shares_the_login_backoff(tmp_path, monkeypatch):
    """#37: the current-password check is a password check, so it is limited
    the same way. A stolen session cannot guess faster than the login page."""
    from app import auth

    auth.reset_login_state()
    with _client(tmp_path, monkeypatch) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        body = {"current_password": "wrong wrong", "new_password": "battery staple"}
        for _ in range(4):
            assert c.post("/api/auth/change", json=body).status_code == 401
        # The fifth failure answers 429 so the Access card can start counting down.
        r = c.post("/api/auth/change", json=body)
        assert r.status_code == 429 and r.headers["Retry-After"] == str(r.json()["retry_after"])
        assert r.json()["retry_after"] >= 1
        # The same backoff now refuses signing in, and the right password too.
        assert c.post("/api/auth/login", json={"password": "correct horse"}).status_code == 429
        r = c.post(
            "/api/auth/change",
            json={"current_password": "correct horse", "new_password": "battery staple"},
        )
        assert r.status_code == 429
        # Nothing was changed while blocked.
        auth.reset_login_state()
        assert c.post("/api/auth/login", json={"password": "correct horse"}).status_code == 200
    auth.reset_login_state()


def test_successful_password_change_forgives_earlier_failures(tmp_path, monkeypatch):
    from app import auth

    auth.reset_login_state()
    with _client(tmp_path, monkeypatch) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        for _ in range(4):
            c.post(
                "/api/auth/change",
                json={"current_password": "wrong wrong", "new_password": "battery staple"},
            )
        r = c.post(
            "/api/auth/change",
            json={"current_password": "correct horse", "new_password": "battery staple"},
        )
        assert r.status_code == 200
        assert auth.login_blocked("testclient") == 0
        assert c.post("/api/auth/login", json={"password": "battery staple"}).status_code == 200
    auth.reset_login_state()


def test_password_change_still_needs_a_session(tmp_path, monkeypatch):
    """The backoff is on top of the session gate, not instead of it, and an
    unauthenticated attempt never consumes the caller's login attempts."""
    from app import auth

    auth.reset_login_state()
    with _client(tmp_path, monkeypatch) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        c.post("/api/auth/logout")
        for _ in range(8):
            r = c.post(
                "/api/auth/change",
                json={"current_password": "correct horse", "new_password": "battery staple"},
            )
            assert r.status_code == 401
        assert auth.login_blocked("testclient") == 0
        assert c.post("/api/auth/login", json={"password": "correct horse"}).status_code == 200
    auth.reset_login_state()
