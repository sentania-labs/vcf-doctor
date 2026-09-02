"""Browser-facing security headers are on every response, UI and API alike."""

from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


def _main(tmp_path, monkeypatch, auth: str = "off"):
    from app import db
    from app.config import settings

    db.reset_for_tests(str(tmp_path / "t.db"))
    monkeypatch.setattr(settings, "auth", auth)
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<title>VCF Doctor</title>")
    (static / "assets" / "app.js").write_text("console.log('hi')")
    monkeypatch.setattr(settings, "static_dir", str(static))
    import importlib

    import app.main as main

    importlib.reload(main)
    return main


def test_headers_present_on_ui_assets_and_api(tmp_path, monkeypatch):
    main = _main(tmp_path, monkeypatch)
    with TestClient(main.app) as c:
        for path in ("/", "/health", "/assets/app.js", "/api/health", "/api/overview"):
            r = c.get(path)
            assert r.status_code == 200, path
            for name, value in EXPECTED.items():
                assert r.headers.get(name) == value, (path, name)
        # API responses are never cached; the UI shell is left to the browser.
        assert c.get("/api/health").headers["cache-control"] == "no-store"
        assert c.get("/api/overview").headers["cache-control"] == "no-store"
        assert c.get("/").headers.get("cache-control") != "no-store"
        # Plain http: no HSTS, or a browser would refuse http for a year.
        assert "strict-transport-security" not in c.get("/").headers
        assert "strict-transport-security" not in c.get("/api/health").headers
        r = c.get("/api/does-not-exist")
        assert r.status_code == 404
        assert r.headers["content-security-policy"] == EXPECTED["content-security-policy"]
        assert r.headers["cache-control"] == "no-store"


def test_headers_present_on_401(tmp_path, monkeypatch):
    main = _main(tmp_path, monkeypatch, auth="on")
    with TestClient(main.app) as c:
        r = c.get("/api/overview")
        assert r.status_code == 401
        assert r.headers["content-security-policy"] == EXPECTED["content-security-policy"]
        assert r.headers["cache-control"] == "no-store"
        r = c.get("/api/does-not-exist")
        assert r.status_code == 401  # auth runs before routing under /api
        assert r.headers["x-frame-options"] == "DENY"


def test_hsts_only_when_the_proxy_says_https(tmp_path, monkeypatch):
    main = _main(tmp_path, monkeypatch)
    # Same wrapper uvicorn applies with --proxy-headers --forwarded-allow-ips '*'.
    with TestClient(ProxyHeadersMiddleware(main.app, trusted_hosts="*")) as c:
        assert "strict-transport-security" not in c.get("/api/health").headers
        plain = c.get("/api/health", headers={"X-Forwarded-Proto": "http"})
        assert "strict-transport-security" not in plain.headers
        for path in ("/", "/api/health"):
            r = c.get(path, headers={"X-Forwarded-Proto": "https"})
            assert r.headers["strict-transport-security"] == "max-age=31536000", path
