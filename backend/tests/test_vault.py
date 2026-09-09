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
    db.reset_for_tests()
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
    return db.fetchone("SELECT password FROM connections WHERE id = %s", (cid,))["password"]


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
        c.execute("UPDATE connections SET password = %s WHERE id = %s", ("legacy", conn.id))
        c.execute(
            "INSERT INTO settings(key, value) VALUES(%s, %s)",
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
        c.execute("UPDATE connections SET password = %s WHERE id = %s", ("legacy", conn.id))
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
        c.execute("UPDATE connections SET password = '' WHERE id = %s", (conn.id,))
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
        c.execute("UPDATE connections SET password = %s WHERE id = %s", ("enc1:oops", conn.id))
        c.execute("DELETE FROM settings WHERE key = %s", (vault.MIGRATED_KEY,))
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


# ---- #48: rotation without re-entering credentials --------------------------


def _set_key(monkeypatch, key: str) -> None:
    monkeypatch.setenv(vault.ENV_KEY, key)
    vault.reset_for_tests()


def test_rekey_moves_every_secret_to_the_new_key(monkeypatch):
    old = Fernet.generate_key().decode()
    _set_key(monkeypatch, old)
    a = _conn(password="first")
    b = _conn(password="second")
    assistant_settings.update_settings({"api_key": SECRET})

    _set_key(monkeypatch, Fernet.generate_key().decode())
    assert store.get_connection(a.id).credentials_unreadable is True

    outcome = vault.rekey(old, "a test")
    assert (outcome.rewritten, outcome.unreadable, outcome.error) == (3, 0, None)
    assert store.get_connection(a.id).password == "first"
    assert store.get_connection(b.id).password == "second"
    assert store.get_connection(a.id).credentials_unreadable is False
    assert assistant_settings.resolve_api_key() == SECRET
    # Everything is stored under the current key, so the old one is now useless.
    assert _raw_password(a.id).startswith(vault.PREFIX)
    assert vault.rekey(old, "a test").rewritten == 0


def test_rekey_with_the_wrong_key_changes_nothing(monkeypatch):
    old = Fernet.generate_key().decode()
    _set_key(monkeypatch, old)
    conn = _conn(password="first")
    before = _raw_password(conn.id)

    _set_key(monkeypatch, Fernet.generate_key().decode())
    outcome = vault.rekey(Fernet.generate_key().decode(), "a test")
    assert outcome.rewritten == 0 and outcome.unreadable == 1
    assert outcome.error and "not supplied" in outcome.error
    assert _raw_password(conn.id) == before
    # The right key still works afterwards.
    assert vault.rekey(old, "a test").rewritten == 1
    assert store.get_connection(conn.id).password == "first"


def test_rekey_accepts_a_passphrase_and_leaves_readable_rows_alone(monkeypatch):
    _set_key(monkeypatch, "correct horse battery staple")
    stale = _conn(password="stale")
    _set_key(monkeypatch, Fernet.generate_key().decode())
    fresh = _conn(password="fresh")
    fresh_raw = _raw_password(fresh.id)

    outcome = vault.rekey("correct horse battery staple", "a test")
    assert outcome.rewritten == 1 and outcome.unreadable == 0
    assert store.get_connection(stale.id).password == "stale"
    assert _raw_password(fresh.id) == fresh_raw  # untouched, already readable


def test_rekey_leaves_legacy_plaintext_to_the_plaintext_migration(monkeypatch):
    old = Fernet.generate_key().decode()
    _set_key(monkeypatch, old)
    conn = _conn(password="p@ss")
    with db.transaction() as c:
        c.execute("UPDATE connections SET password = %s WHERE id = %s", ("legacy", conn.id))
    _set_key(monkeypatch, Fernet.generate_key().decode())
    assert vault.rekey(old, "a test").rewritten == 0
    assert _raw_password(conn.id) == "legacy"
    assert vault.migrate_plaintext() == 1
    assert store.get_connection(conn.id).password == "legacy"


def test_startup_rotates_from_the_previous_env_key(monkeypatch):
    from app.main import app

    old = Fernet.generate_key().decode()
    _set_key(monkeypatch, old)
    conn = _conn(password="first")
    assistant_settings.update_settings({"api_key": SECRET})
    with TestClient(app):
        pass  # first boot writes the plaintext migration marker

    _set_key(monkeypatch, Fernet.generate_key().decode())
    monkeypatch.setenv(vault.ENV_PREVIOUS_KEY, old)
    with TestClient(app) as client:
        assert store.get_connection(conn.id).password == "first"
        assert assistant_settings.resolve_api_key() == SECRET
        body = client.get("/api/settings/encryption").json()
        assert body["unreadable_connections"] == []
        assert body["key_previous_env_var"] == vault.ENV_PREVIOUS_KEY
        assert body["last_rekey"]["rewritten"] == 2
        assert body["last_rekey"]["error"] is None
        assert vault.ENV_PREVIOUS_KEY in body["last_rekey"]["source"]
        for path in ("/api/settings/encryption", "/api/connections"):
            assert old not in client.get(path).text


def test_key_file_rotation_is_offered_but_never_automatic(monkeypatch):
    """Moving from the generated key file to a sealed secret costs no re-entry,
    but only when the operator asks: an env key set by mistake must stay
    recoverable by unsetting it, which a silent re-encryption would prevent."""
    from app.main import app

    conn = _conn(password="first")
    with TestClient(app):
        pass
    assert vault.key_source() == "file"
    file_key = vault.key_file_path().read_text().strip()

    _set_key(monkeypatch, Fernet.generate_key().decode())
    assert vault.key_file_path().exists()  # left in place, never deleted for us
    with TestClient(app) as client:
        # Startup on its own changes nothing: the password still needs re-entry.
        body = client.get("/api/settings/encryption").json()
        assert body["key_source"] == "env" and body["unreadable_connections"] == [conn.id]
        assert body["last_rekey"] is None
        assert body["previous_key_file"] == str(vault.key_file_path())
        assert file_key not in client.get("/api/settings/encryption").text

        r = client.post("/api/settings/encryption/rekey")
        assert r.status_code == 200, r.text
        assert r.json()["rewritten"] == 1 and file_key not in r.text
        assert store.get_connection(conn.id).password == "first"
        assert r.json()["status"]["unreadable_connections"] == []
        assert "key file" in r.json()["status"]["last_rekey"]["source"]
        # Nothing left to move: a repeat is a harmless no-op.
        again = client.post("/api/settings/encryption/rekey").json()
        assert again["ok"] is True and again["rewritten"] == 0
        assert "Nothing to do" in again["message"]


def test_unreadable_leftover_key_file_does_not_advise_deleting_it(monkeypatch):
    """The leftover file is the only copy of the previous key, and the active
    key is an env value, so the active-key advice to remove it and re-enter
    would destroy the one thing that can still open the stored secrets."""
    from app.main import app

    conn = _conn(password="first")
    with TestClient(app):
        pass

    _set_key(monkeypatch, Fernet.generate_key().decode())
    vault.key_file_path().write_text("not a key\n")
    with TestClient(app) as client:
        r = client.post("/api/settings/encryption/rekey")
        assert r.status_code == 503, r.text
        detail = r.json()["detail"]
        assert "remove it" not in detail
        assert vault.ENV_PREVIOUS_KEY in detail
        assert store.get_connection(conn.id).credentials_unreadable is True


def test_key_file_rotation_needs_a_key_file(monkeypatch):
    from app.main import app

    _set_key(monkeypatch, Fernet.generate_key().decode())
    _conn(password="first")
    with TestClient(app) as client:
        assert client.get("/api/settings/encryption").json()["previous_key_file"] is None
        r = client.post("/api/settings/encryption/rekey")
        assert r.status_code == 503 and "no generated key file" in r.json()["detail"]


def test_startup_reports_a_previous_key_that_opens_nothing(monkeypatch):
    from app.main import app

    old = Fernet.generate_key().decode()
    _set_key(monkeypatch, old)
    conn = _conn(password="first")
    with TestClient(app):
        pass

    _set_key(monkeypatch, Fernet.generate_key().decode())
    monkeypatch.setenv(vault.ENV_PREVIOUS_KEY, Fernet.generate_key().decode())
    with TestClient(app) as client:
        body = client.get("/api/settings/encryption").json()
        assert body["unreadable_connections"] == [conn.id]
        assert body["last_rekey"]["rewritten"] == 0
        assert "not supplied" in body["last_rekey"]["error"]


def test_startup_without_a_previous_key_records_nothing(monkeypatch):
    from app.main import app

    _set_key(monkeypatch, Fernet.generate_key().decode())
    _conn(password="first")
    with TestClient(app) as client:
        assert client.get("/api/settings/encryption").json()["last_rekey"] is None


def test_startup_records_an_outcome_even_when_nothing_needed_moving(monkeypatch):
    """A rotation that found nothing to move is still recorded, so the card's
    timestamp proves this restart saw the previous key. Without it an older
    record stands in for a rotation that never ran, and the operator drops the
    previous key while every secret is still encrypted under it."""
    from datetime import UTC, datetime

    from app.main import app

    key = Fernet.generate_key().decode()
    _set_key(monkeypatch, key)
    _conn(password="first")
    with TestClient(app):
        pass
    assert vault.last_rekey() is None

    before = datetime.now(UTC).isoformat(timespec="seconds")
    monkeypatch.setenv(vault.ENV_PREVIOUS_KEY, key)
    vault.reset_for_tests()
    with TestClient(app) as client:
        last = client.get("/api/settings/encryption").json()["last_rekey"]
        assert last is not None
        assert (last["rewritten"], last["unreadable"], last["error"]) == (0, 0, None)
        assert vault.ENV_PREVIOUS_KEY in last["source"]
        assert last["at"] >= before
        assert key not in client.get("/api/settings/encryption").text


def test_partial_rotation_reports_both_what_moved_and_what_was_left(monkeypatch):
    """One secret under the supplied previous key, one under an older key. The
    reported sentence must carry both counts: a rotation that moved something
    is not a total failure, and the operator needs to know what did move."""
    from app.main import app

    first = Fernet.generate_key().decode()
    _set_key(monkeypatch, first)
    stranded = _conn(password="under-first")
    with TestClient(app):
        pass  # first boot writes the plaintext migration marker
    second = Fernet.generate_key().decode()
    _set_key(monkeypatch, second)
    moved = _conn(password="under-second")

    _set_key(monkeypatch, Fernet.generate_key().decode())
    monkeypatch.setenv(vault.ENV_PREVIOUS_KEY, second)
    vault.reset_for_tests()
    with TestClient(app) as client:
        last = client.get("/api/settings/encryption").json()["last_rekey"]
        assert (last["rewritten"], last["unreadable"]) == (1, 1)
        assert "Re-encrypted 1 stored secret under the current key." in last["message"]
        assert "1 stored secret is encrypted with a key that was not supplied" in last["message"]
        assert store.get_connection(moved.id).password == "under-second"
        assert store.get_connection(stranded.id).credentials_unreadable is True
        for key in (first, second):
            assert key not in client.get("/api/settings/encryption").text


def test_rekey_endpoint_never_accepts_key_material(monkeypatch):
    """Rotating from a supplied key is a deployment action (see the startup test
    above), never something the interface takes. A key sent in the request body
    rotates nothing: the endpoint only ever uses the generated key file, and a
    deployment that never had one has nothing to rotate from."""
    from app.main import app

    old = Fernet.generate_key().decode()
    _set_key(monkeypatch, old)
    conn = _conn(password="first")
    with TestClient(app):
        pass
    assert not vault.key_file_path().exists()  # the env key was set from the start

    _set_key(monkeypatch, Fernet.generate_key().decode())
    with TestClient(app) as client:
        assert client.get("/api/settings/encryption").json()["unreadable_connections"] == [conn.id]
        r = client.post("/api/settings/encryption/rekey", json={"previous_key": old})
        assert r.status_code == 503 and "no generated key file" in r.json()["detail"]
        assert store.get_connection(conn.id).credentials_unreadable is True


def test_rekey_moves_what_it_can_and_names_what_it_could_not(monkeypatch):
    """Two secrets under two different old keys: supplying one moves that one,
    reports the other, and commits the rewrite and the recorded outcome
    together."""
    from app.main import app

    first = Fernet.generate_key().decode()
    _set_key(monkeypatch, first)
    a = _conn(password="under-first")
    with TestClient(app):
        pass  # first boot writes the plaintext migration marker
    second = Fernet.generate_key().decode()
    _set_key(monkeypatch, second)
    b = _conn(password="under-second")

    _set_key(monkeypatch, Fernet.generate_key().decode())
    outcome = vault.rekey(first, "a test")
    assert (outcome.rewritten, outcome.unreadable) == (1, 1)
    assert "1 stored secret is encrypted with a key that was not supplied" in outcome.error
    assert store.get_connection(a.id).password == "under-first"
    assert store.get_connection(b.id).credentials_unreadable is True
    # The rewrite and the outcome describing it landed in the same transaction.
    recorded = vault.last_rekey()
    assert (recorded.rewritten, recorded.unreadable) == (1, 1)
    # The rest moves once its own key is supplied.
    rest = vault.rekey(second, "a test")
    assert rest.rewritten == 1 and rest.error is None
    assert store.get_connection(b.id).password == "under-second"
