"""Secrets at rest: vCenter passwords and the Anthropic key are encrypted,
plaintext rows migrate on startup, and a lost key degrades to "re-enter
credentials" rather than a crash."""

import base64
import json
import os
import stat

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import db, vault
from app.assistant import settings as assistant_settings
from app.collectors.registry import CredentialsUnreadable, get_collector
from app.models import ConnectionCreate
from app.snapshots import store

SECRET = "sk-ant-test-not-a-real-key-0000"


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    db.reset_for_tests(str(tmp_path / "t.db"))
    vault.reset_for_tests()
    monkeypatch.delenv(vault.ENV_KEY, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield
    vault.reset_for_tests()


def _conn(password="p@ss", kind="vcenter"):
    return store.create_connection(
        ConnectionCreate(name="c", host="fixture", username="u", password=password, kind=kind)
    )


def _raw_password(cid: str) -> str:
    return db.fetchone("SELECT password FROM connections WHERE id = ?", (cid,))["password"]


def test_key_file_generated_with_0600_and_reused(tmp_path):
    path = vault.key_file_path()
    assert not path.exists()
    token = vault.encrypt("x")
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert vault.key_source() == "file"
    first = path.read_text()
    # Second "process": cache dropped, same key file picked up, old token still opens.
    vault.reset_for_tests()
    assert vault.decrypt(token) == "x"
    assert path.read_text() == first


def test_env_key_wins_and_no_file_is_written(monkeypatch):
    monkeypatch.setenv(vault.ENV_KEY, Fernet.generate_key().decode())
    token = vault.encrypt("x")
    assert vault.key_source() == "env"
    assert not vault.key_file_path().exists()
    assert vault.decrypt(token) == "x"


def test_env_passphrase_is_accepted(monkeypatch):
    monkeypatch.setenv(vault.ENV_KEY, "correct horse battery staple")
    assert vault.decrypt(vault.encrypt("x")) == "x"
    assert vault.key_source() == "env"


def test_password_stored_encrypted_and_read_back():
    conn = _conn()
    raw = _raw_password(conn.id)
    assert raw.startswith(vault.PREFIX) and "p@ss" not in raw
    assert store.get_connection(conn.id).password == "p@ss"
    assert store.get_connection(conn.id).credentials_unreadable is False
    store.update_connection(conn.id, {"password": "new"})
    assert "new" not in _raw_password(conn.id)
    assert store.get_connection(conn.id).password == "new"
    # Empty password keeps the stored one and leaves it encrypted.
    store.update_connection(conn.id, {"password": ""})
    assert store.get_connection(conn.id).password == "new"


def test_assistant_key_stored_encrypted():
    assistant_settings.update_settings({"api_key": SECRET})
    raw = db.get_setting(assistant_settings.API_KEY_KEY)
    assert raw.startswith(vault.PREFIX) and SECRET not in raw
    assert assistant_settings.resolve_api_key() == SECRET
    s = assistant_settings.get_settings()
    assert s.api_key_set is True and s.api_key_unreadable is False


def test_migration_encrypts_plaintext_rows_once():
    conn = _conn()
    with db.transaction() as c:
        c.execute("UPDATE connections SET password = ? WHERE id = ?", ("legacy", conn.id))
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?)",
            (assistant_settings.API_KEY_KEY, json.dumps(SECRET)),
        )
    assert vault.migrate_plaintext() == 2
    assert _raw_password(conn.id).startswith(vault.PREFIX)
    assert db.get_setting(assistant_settings.API_KEY_KEY).startswith(vault.PREFIX)
    assert store.get_connection(conn.id).password == "legacy"
    assert assistant_settings.resolve_api_key() == SECRET
    assert vault.migrate_plaintext() == 0  # idempotent


def _rotate_key(monkeypatch):
    monkeypatch.setenv(vault.ENV_KEY, Fernet.generate_key().decode())
    vault.reset_for_tests()


def test_wrong_key_marks_connection_needing_credentials(monkeypatch):
    conn = _conn()
    assistant_settings.update_settings({"api_key": SECRET})
    _rotate_key(monkeypatch)

    loaded = store.get_connection(conn.id)
    assert loaded.credentials_unreadable is True and loaded.password == ""
    assert store.public(loaded).needs_credentials is True
    with pytest.raises(CredentialsUnreadable):
        get_collector(loaded)

    s = assistant_settings.get_settings()
    assert s.api_key_set is False and s.api_key_unreadable is True
    assert assistant_settings.resolve_api_key() is None

    # Re-entering stores under the new key and clears the flag.
    store.update_connection(conn.id, {"password": "again"})
    assert store.get_connection(conn.id).credentials_unreadable is False
    assert store.get_connection(conn.id).password == "again"
    assistant_settings.update_settings({"api_key": SECRET})
    assert assistant_settings.get_settings().api_key_unreadable is False
    # Migration leaves already-encrypted (even unreadable) rows alone.
    assert vault.migrate_plaintext() == 0


def test_wrong_key_env_fallback_for_assistant(monkeypatch):
    assistant_settings.update_settings({"api_key": SECRET})
    _rotate_key(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-0000")
    assert assistant_settings.resolve_api_key() == "sk-ant-env-0000"


def test_api_surfaces_status_and_never_the_key(monkeypatch):
    from app.main import app

    conn = _conn()
    assistant_settings.update_settings({"api_key": SECRET})
    with TestClient(app) as client:
        r = client.get("/api/settings/encryption")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True and body["key_source"] == "file"
        assert body["key_file"].endswith(".key")
        assert body["unreadable_connections"] == [] and body["assistant_key_unreadable"] is False
        assert vault.key_file_path().read_text().strip() not in r.text

    _rotate_key(monkeypatch)
    with TestClient(app) as client:
        body = client.get("/api/settings/encryption").json()
        assert body["key_source"] == "env" and body["key_file"] is None
        assert body["unreadable_connections"] == [conn.id]
        assert body["assistant_key_unreadable"] is True
        assert os.environ[vault.ENV_KEY] not in client.get("/api/settings").text
        conns = client.get("/api/connections").json()
        assert conns[0]["needs_credentials"] is True and "password" not in conns[0]
        assert client.get("/api/settings").json()["assistant"]["api_key_unreadable"] is True
        # Test and scan fail with a clear message instead of crashing.
        t = client.post(f"/api/connections/{conn.id}/test").json()
        assert t["ok"] is False and "re-enter" in t["message"]
        run = client.post("/api/scan", json={"connection_id": conn.id}).json()[0]
        assert run["status"] == "skipped" and "re-enter" in run["error"]
        assert client.get(f"/api/connections/{conn.id}/schedule").json()["last_status"] == "skipped"
        for path in ("/api/settings/encryption", "/api/connections", "/api/scans"):
            assert os.environ[vault.ENV_KEY] not in client.get(path).text
        # Re-enter through the API; everything clears.
        r = client.put(f"/api/connections/{conn.id}", json={"password": "again"})
        assert r.json()["needs_credentials"] is False
        assert client.get("/api/settings/encryption").json()["unreadable_connections"] == []


def test_startup_migrates_plaintext(tmp_path):
    from app.main import app

    conn = _conn()
    with db.transaction() as c:
        c.execute("UPDATE connections SET password = ? WHERE id = ?", ("legacy", conn.id))
    with TestClient(app):
        pass
    assert _raw_password(conn.id).startswith(vault.PREFIX)
    assert store.get_connection(conn.id).password == "legacy"


def test_env_fallback_is_reported_not_flagged_as_broken(monkeypatch):
    from app.main import app

    assistant_settings.update_settings({"api_key": SECRET})
    with TestClient(app):
        pass  # first boot writes the migration marker, as every deployment does
    _rotate_key(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-0000")
    with TestClient(app) as client:
        body = client.get("/api/settings/encryption").json()
        assert body["assistant_key_unreadable"] is True and body["assistant_env_fallback"] is True
        a = client.get("/api/settings").json()["assistant"]
        assert a["api_key_set"] is True and a["api_key_unreadable"] is True


def test_corrupt_key_file_degrades_instead_of_crashing():
    from app.main import app

    conn = _conn()
    vault.reset_for_tests()
    vault.key_file_path().write_text("not a key\n")
    # Startup, listing, and status all survive; the connection is flagged.
    with TestClient(app) as client:
        body = client.get("/api/settings/encryption").json()
        assert body["key_error"] and "corrupt" in body["key_error"]
        assert body["unreadable_connections"] == [conn.id]
        assert "not a key" not in body["key_error"]  # names the path, never the contents
        # Saving a secret is refused with a clear message, not a 500.
        r = client.put(f"/api/connections/{conn.id}", json={"password": "x"})
        assert r.status_code == 503 and "key file" in r.json()["detail"]
        r = client.put("/api/settings", json={"assistant": {"api_key": "sk-ant-new"}})
        assert r.status_code == 503


def test_empty_password_migrates_and_roundtrips():
    conn = _conn(password="")
    with db.transaction() as c:
        c.execute("UPDATE connections SET password = '' WHERE id = ?", (conn.id,))
    assert vault.migrate_plaintext() == 1
    loaded = store.get_connection(conn.id)
    assert loaded.password == "" and loaded.credentials_unreadable is False


def test_key_file_write_is_atomic_no_temp_left_behind():
    vault.encrypt("x")
    path = vault.key_file_path()
    siblings = path.parent.iterdir()
    leftovers = [p.name for p in siblings if p.name.startswith(path.name) and p != path]
    assert leftovers == []


def test_first_migration_handles_legacy_plaintext_that_looks_encrypted():
    """A legacy plaintext password starting with the prefix is still plaintext."""
    conn = _conn()
    with db.transaction() as c:
        c.execute("UPDATE connections SET password = ? WHERE id = ?", ("enc1:oops", conn.id))
        c.execute("DELETE FROM settings WHERE key = ?", (vault.MIGRATED_KEY,))
    assert vault.migrate_plaintext() == 1
    loaded = store.get_connection(conn.id)
    assert loaded.password == "enc1:oops" and loaded.credentials_unreadable is False
    assert db.get_setting(vault.MIGRATED_KEY) == 1
    assert vault.migrate_plaintext() == 0


def test_first_migration_does_not_double_encrypt_genuine_tokens():
    conn = _conn(password="real")
    assistant_settings.update_settings({"api_key": SECRET})
    assert db.get_setting(vault.MIGRATED_KEY) is None
    assert vault.migrate_plaintext() == 0
    assert store.get_connection(conn.id).password == "real"
    assert assistant_settings.resolve_api_key() == SECRET


def test_fixture_connection_never_needs_credentials(monkeypatch):
    conn = _conn(password="", kind="fixture")
    _rotate_key(monkeypatch)
    loaded = store.get_connection(conn.id)
    assert loaded.credentials_unreadable is False and loaded.password == ""
    assert store.public(loaded).needs_credentials is False


def test_passphrase_key_is_stretched_not_hashed_once(monkeypatch):
    import hashlib

    from cryptography.fernet import Fernet

    monkeypatch.setenv(vault.ENV_KEY, "correct horse battery staple")
    token = vault.encrypt("x")
    from cryptography.fernet import InvalidToken

    digest = hashlib.sha256(b"correct horse battery staple").digest()
    naive = Fernet(base64.urlsafe_b64encode(digest))
    with pytest.raises(InvalidToken):
        naive.decrypt(token[len(vault.PREFIX) :].encode())
    assert vault.decrypt(token) == "x"
