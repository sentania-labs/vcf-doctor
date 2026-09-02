"""Login backoff: keyed per client address, bounded, with a process-wide ceiling."""

import pytest
from fastapi.testclient import TestClient

from app import auth, db, proxies


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth", "on")
    monkeypatch.setattr(settings, "trusted_proxies", "")
    auth.reset_login_state()
    yield
    auth.reset_login_state()


# ---- pure limiter ----------------------------------------------------------


def test_failures_are_counted_per_address():
    for _ in range(5):
        auth.record_login("10.0.0.1", False)
    assert auth.login_blocked("10.0.0.1") >= 1
    assert auth.login_blocked("10.0.0.2") == 0


def test_success_clears_only_that_address():
    for ip in ("10.0.0.1", "10.0.0.2"):
        for _ in range(5):
            auth.record_login(ip, False)
    auth.record_login("10.0.0.1", True)
    assert auth.login_blocked("10.0.0.1") == 0
    assert auth.login_blocked("10.0.0.2") >= 1


def test_wait_grows_and_is_capped(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    for _ in range(5):
        auth.record_login("10.0.0.1", False)
    assert auth.login_blocked("10.0.0.1") == 1  # 2**0
    for _ in range(3):
        auth.record_login("10.0.0.1", False)
    assert auth.login_blocked("10.0.0.1") == 8  # 2**3
    for _ in range(10):
        auth.record_login("10.0.0.1", False)
    assert auth.login_blocked("10.0.0.1") == auth._BACKOFF_MAX
    now[0] += 0.5
    assert auth.login_blocked("10.0.0.1") == auth._BACKOFF_MAX  # 59.5 rounds up
    now[0] += auth._BACKOFF_MAX
    assert auth.login_blocked("10.0.0.1") == 0


def test_store_is_bounded_and_evicts_oldest(monkeypatch):
    monkeypatch.setattr(auth, "MAX_TRACKED", 50)
    monkeypatch.setattr(auth, "GLOBAL_LIMIT", 10_000)
    auth.reset_login_state()
    for i in range(500):
        auth.record_login(f"10.1.{i // 256}.{i % 256}", False)
    assert auth.tracked_addresses() == 50
    # The first address was evicted; a new failure starts from zero.
    auth.record_login("10.1.0.0", False)
    assert auth.login_blocked("10.1.0.0") == 0


def test_entries_expire(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    for _ in range(5):
        auth.record_login("10.0.0.1", False)
    now[0] += auth.ENTRY_TTL + 1
    assert auth.login_blocked("10.0.0.1") == 0
    assert auth.tracked_addresses() == 0


def test_global_ceiling_blocks_every_address(monkeypatch):
    monkeypatch.setattr(auth, "GLOBAL_LIMIT", 10)
    auth.reset_login_state()  # re-size the deque under the new limit
    for i in range(10):
        auth.record_login(f"10.2.0.{i}", False)  # one failure each, none locked alone
    assert auth.login_blocked("10.3.0.1") >= 1
    auth.record_login("10.3.0.1", True)  # a success does not lift the ceiling
    assert auth.login_blocked("10.3.0.1") >= 1


def test_attempt_is_reserved_before_the_password_check():
    """A burst of concurrent guesses cannot all pass the check: the attempt
    counts from begin_attempt, not from when the result is known."""
    for _ in range(5):
        wait, _stamp = auth.begin_attempt("10.0.0.1")
        assert wait == 0
    wait, _stamp = auth.begin_attempt("10.0.0.1")
    assert wait >= 1
    # A success forgives the reservation for that address and the global window.
    auth.reset_login_state()
    wait, stamp = auth.begin_attempt("10.0.0.2")
    auth.finish_attempt("10.0.0.2", stamp, True)
    assert auth.tracked_addresses() == 0 and len(auth._recent_failures) == 0


def test_env_override_rejects_trust_everyone_and_fails_closed(monkeypatch):
    from app.config import settings

    with pytest.raises(ValueError, match="every address"):
        proxies.parse_list(["0.0.0.0/0"])
    with pytest.raises(ValueError, match="every address"):
        proxies.parse_list("::/0")
    monkeypatch.setattr(settings, "trusted_proxies", "0.0.0.0/0")
    assert proxies.effective() == ([], "env")


def test_ipv4_mapped_ipv6_peer_matches_ipv4_network():
    import ipaddress

    nets = [ipaddress.ip_network("10.0.0.0/8")]
    assert proxies.is_trusted("::ffff:10.0.0.7", nets)
    assert not proxies.is_trusted("::ffff:192.0.2.7", nets)


# ---- through the API -------------------------------------------------------


def _app(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    import importlib

    import app.main as main

    importlib.reload(main)
    return main.app


def _lock_out(c, ip, n=5):
    last = None
    for _ in range(n):
        last = c.post(
            "/api/auth/login", json={"password": "wrong wrong"}, headers={"X-Forwarded-For": ip}
        )
    return last


def test_concurrent_guesses_do_not_slip_past_the_counter(tmp_path, monkeypatch):
    """Regression for the check-then-act race: a burst of 40 guesses during
    one slow password check must verify at most five of them."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    app = _app(tmp_path)
    real = auth.verify_password
    verified = []
    gate = threading.Event()

    def slow_verify(pw):
        verified.append(pw)
        gate.wait(2)  # hold every checker until the burst has been issued
        return real(pw)

    with TestClient(app, client=("10.0.0.1", 5000)) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        c.post("/api/auth/logout")
        monkeypatch.setattr(auth, "verify_password", slow_verify)
        guesses = ["wrong wrong"] * 39 + ["correct horse"]

        def go(pw):
            return c.post("/api/auth/login", json={"password": pw}).status_code

        with ThreadPoolExecutor(max_workers=40) as pool:
            futures = [pool.submit(go, pw) for pw in guesses]
            time.sleep(0.5)
            gate.set()
            codes = [f.result() for f in futures]
    assert len(verified) <= 5, verified
    assert codes.count(200) == 0
    assert codes.count(429) >= 35


def test_login_reports_wait_in_header_and_body(tmp_path):
    app = _app(tmp_path)
    with TestClient(app, client=("10.0.0.1", 5000)) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        c.post("/api/auth/logout")
        r = _lock_out(c, "ignored")
        assert r.status_code == 429
        body = r.json()
        assert body["retry_after"] >= 1
        assert r.headers["Retry-After"] == str(body["retry_after"])
        assert "try again in" in body["detail"]


def test_untrusted_peer_headers_are_ignored(tmp_path):
    """Default: nobody is trusted, so X-Forwarded-For does not split the bucket."""
    app = _app(tmp_path)
    with TestClient(app, client=("10.0.0.1", 5000)) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        c.post("/api/auth/logout")
        _lock_out(c, "203.0.113.5")
        r = c.post(
            "/api/auth/login",
            json={"password": "correct horse"},
            headers={"X-Forwarded-For": "203.0.113.6"},
        )
        assert r.status_code == 429


def test_trusted_proxy_splits_the_bucket_per_client(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    app = _app(tmp_path)
    with TestClient(app, client=("10.0.0.1", 5000)) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        c.post("/api/auth/logout")
        _lock_out(c, "203.0.113.5")
        r = c.post(
            "/api/auth/login",
            json={"password": "correct horse"},
            headers={"X-Forwarded-For": "203.0.113.5"},
        )
        assert r.status_code == 429
        # A different client behind the same ingress is not locked out.
        r = c.post(
            "/api/auth/login",
            json={"password": "correct horse"},
            headers={"X-Forwarded-For": "203.0.113.6"},
        )
        assert r.status_code == 200


def test_trusted_proxies_from_settings_page_apply_live(tmp_path):
    app = _app(tmp_path)
    with TestClient(app, client=("10.0.0.1", 5000)) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        r = c.put("/api/settings/trusted-proxies", json={"trusted_proxies": ["10.0.0.1"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["trusted_proxies"] == ["10.0.0.1/32"] and body["source"] == "settings"
        assert body["stored"] == ["10.0.0.1/32"] and body["env_problem"] is None
        # Trust is decided when a request arrives, so the save itself still
        # reports the old decision; the card re-reads after saving.
        assert body["peer"] == "10.0.0.1" and body["peer_trusted"] is False
        body = c.get("/api/settings/trusted-proxies").json()
        assert body["peer"] == "10.0.0.1" and body["peer_trusted"] is True
        c.post("/api/auth/logout")
        _lock_out(c, "203.0.113.5")
        r = c.post(
            "/api/auth/login",
            json={"password": "correct horse"},
            headers={"X-Forwarded-For": "203.0.113.6"},
        )
        assert r.status_code == 200


def test_trusted_proxies_validation_and_env_override(tmp_path, monkeypatch):
    from app.config import settings

    app = _app(tmp_path)
    with TestClient(app, client=("10.0.0.1", 5000)) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        r = c.put("/api/settings/trusted-proxies", json={"trusted_proxies": ["not-an-ip"]})
        assert r.status_code == 400 and "not-an-ip" in r.json()["detail"]
        assert c.get("/api/settings/trusted-proxies").json()["trusted_proxies"] == []
        c.put("/api/settings/trusted-proxies", json={"trusted_proxies": ["192.168.1.1"]})
        monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8, 172.16.0.0/12")
        body = c.get("/api/settings/trusted-proxies").json()
        assert body["source"] == "env"
        assert body["trusted_proxies"] == ["10.0.0.0/8", "172.16.0.0/12"]
        assert body["stored"] == ["192.168.1.1/32"]
        monkeypatch.setattr(settings, "trusted_proxies", "garbage")
        body = c.get("/api/settings/trusted-proxies").json()
        assert body["source"] == "env" and "garbage" in body["env_problem"]
        assert body["trusted_proxies"] == []  # fails closed, stored list not used
        # The page can point at the ingress: this peer is untrusted and sent forwarded headers.
        body = c.get(
            "/api/settings/trusted-proxies", headers={"X-Forwarded-For": "203.0.113.9"}
        ).json()
        assert body["peer"] == "10.0.0.1" and body["peer_trusted"] is False
        assert body["ignored_forwarded_headers"] is True and body["scheme"] == "http"
    with TestClient(app) as c:
        assert c.get("/api/settings/trusted-proxies").status_code == 401


def test_rightmost_untrusted_hop_wins():
    import ipaddress

    nets = [ipaddress.ip_network("10.0.0.0/8")]
    # Client, then an untrusted intermediate that the trusted ingress recorded.
    assert proxies.resolve_client("10.0.0.1", ["198.51.100.9, 203.0.113.7"], nets) == "203.0.113.7"
    # Spoofed leading entry is ignored: the rightmost non-trusted hop is the client.
    assert (
        proxies.resolve_client("10.0.0.1", ["1.2.3.4", "203.0.113.7, 10.0.0.2"], nets)
        == "203.0.113.7"
    )
    # Every hop trusted: best guess is the leftmost.
    assert proxies.resolve_client("10.0.0.1", ["10.0.0.3, 10.0.0.2"], nets) == "10.0.0.3"
    # Ports and IPv6 brackets are stripped.
    assert proxies.resolve_client("10.0.0.1", ["[2001:db8::1]:443"], nets) == "2001:db8::1"
    assert proxies.resolve_client("10.0.0.1", ["203.0.113.7:12345"], nets) == "203.0.113.7"
    # Untrusted peer: headers ignored.
    assert proxies.resolve_client("192.0.2.1", ["203.0.113.7"], nets) == "192.0.2.1"
    assert proxies.resolve_client("testclient", ["203.0.113.7"], nets) == "testclient"


def test_limiter_key_is_never_header_sized(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    app = _app(tmp_path)
    with TestClient(app, client=("10.0.0.1", 5000)) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        c.post("/api/auth/logout")
        huge = "x" * 4000
        for _ in range(5):
            c.post("/api/auth/login", json={"password": "bad"}, headers={"X-Forwarded-For": huge})
        assert auth.tracked_addresses() == 1
        assert all(len(k) <= 64 for k in auth._per_ip)


def test_settings_page_reports_the_tcp_peer_not_the_forwarded_client(tmp_path, monkeypatch):
    """Codex round: behind a trusted ingress request.client is the visitor;
    the page must still report the ingress as the (trusted) peer."""
    from app.config import settings

    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    app = _app(tmp_path)
    with TestClient(app, client=("10.0.0.1", 5000)) as c:
        c.post("/api/auth/setup", json={"password": "correct horse"})
        body = c.get(
            "/api/settings/trusted-proxies",
            headers={"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"},
        ).json()
        assert body["peer"] == "10.0.0.1" and body["peer_trusted"] is True
        assert body["ignored_forwarded_headers"] is False and body["scheme"] == "https"
