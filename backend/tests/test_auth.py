from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch, **env):
    from app import db
    from app.config import settings

    db.reset_for_tests(str(tmp_path / "t.db"))
    monkeypatch.setattr(settings, "auth", "on")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import importlib

    import app.main as main

    importlib.reload(main)
    return TestClient(main.app)


def test_first_run_setup_login_logout_change(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        assert c.get("/api/health").status_code == 200
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

    db.reset_for_tests(str(tmp_path / "t.db"))
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

    auth.record_login(True)  # reset process state
    with _client(tmp_path, monkeypatch) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        c.post("/api/auth/logout")
        for _ in range(5):
            assert c.post("/api/auth/login", json={"password": "wrong wrong"}).status_code == 401
        r = c.post("/api/auth/login", json={"password": "correct horse"})
        assert r.status_code == 429 and "Retry-After" in r.headers
    auth.record_login(True)


def test_docs_are_not_public(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        for p in ("/docs", "/redoc", "/openapi.json"):
            assert "openapi" not in c.get(p).text.lower()
