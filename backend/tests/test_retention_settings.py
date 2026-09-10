"""retention_policy on /api/settings: validation, partial merge, legacy count ignored."""

from datetime import UTC

import pytest
from fastapi.testclient import TestClient

from app import db, scheduler, timezones
from app.config import Settings, settings
from app.main import app
from app.snapshots import store


@pytest.fixture()
def client(tmp_path):
    db.reset_for_tests()
    scheduler._begin_startup()
    scheduler.startup_maintenance()
    with TestClient(app) as c:
        yield c


def _put(client, policy):
    return client.put("/api/settings", json={"retention_policy": policy})


def test_defaults_come_from_config_and_old_count_is_ignored(client):
    db.set_setting("retention", 2)  # a pre-tier database that only has the old count
    body = client.get("/api/settings").json()
    default_timezone = store.default_retention_policy().timezone
    assert body["retention_policy"] == {
        "recent_days": 14,
        "hourly_days": 30,
        "daily_days": 365,
        "timezone": default_timezone,
    }
    assert body["event_policy"] == {"retention_hours": 48, "row_cap": 250000}
    assert body["event_policy_default_limit"] is None
    # PostgreSQL's autovacuum reclaims space, so there is no maintenance card.
    assert "event_maintenance" not in body
    stored_event_policy = db.get_setting("event_policy")
    assert stored_event_policy["retention_hours"] == 48
    assert stored_event_policy["row_cap"] == 250000
    assert "retention" not in body


def test_partial_update_merges_and_persists(client):
    r = _put(client, {"hourly_days": 60, "daily_days": 400})
    assert r.status_code == 200, r.text
    assert r.json()["retention_policy"] == {
        "recent_days": 14,
        "hourly_days": 60,
        "daily_days": 400,
        "timezone": settings.retention_timezone,
    }
    assert db.get_setting("retention_policy") == {
        "recent_days": 14,
        "hourly_days": 60,
        "daily_days": 400,
        "timezone": settings.retention_timezone,
    }
    # Equal tiers are allowed (a tier of zero width simply does nothing).
    assert _put(client, {"recent_days": 60}).status_code == 200


@pytest.mark.parametrize(
    ("policy", "fragment"),
    [
        ({"recent_days": 0}, "recent_days"),
        ({"daily_days": -5}, "daily_days"),
        ({"recent_days": "14"}, "integer"),
        ({"recent_days": 1.5}, "integer"),
        ({"recent_days": True}, "integer"),
        ({"recent_days": 31}, "recent <= hourly <= daily"),  # above hourly_days=30
        ({"daily_days": 20}, "recent <= hourly <= daily"),  # below hourly_days=30
        ({"weekly_days": 3}, "unknown retention_policy keys"),
        ({"timezone": "Mars/Olympus"}, "unknown timezone"),
        ({"timezone": 5}, "IANA zone name"),
    ],
)
def test_rejects_bad_policies_with_400(client, policy, fragment):
    r = _put(client, policy)
    assert r.status_code == 400, r.text
    assert fragment in r.json()["detail"]
    # Nothing was stored.
    assert client.get("/api/settings").json()["retention_policy"]["recent_days"] == 14


def test_invalid_stored_policy_falls_back_to_defaults(client):
    db.set_setting("retention_policy", {"recent_days": 99, "hourly_days": 1, "daily_days": 1})
    assert client.get("/api/settings").json()["retention_policy"]["recent_days"] == 14


def test_event_policy_partial_update_persists_and_validates(client):
    r = client.put("/api/settings", json={"event_policy": {"retention_hours": 72}})
    assert r.status_code == 200
    assert r.json()["event_policy"] == {"retention_hours": 72, "row_cap": 250000}
    assert db.get_setting("event_policy") == {"retention_hours": 72, "row_cap": 250000}

    for bad in ({"retention_hours": 0}, {"row_cap": 999}, {"row_cap": True}):
        assert client.put("/api/settings", json={"event_policy": bad}).status_code == 400


def test_clamped_event_default_is_visible_until_an_operator_saves(client, monkeypatch):
    from app.events import store as events_store

    monkeypatch.setattr(settings, "event_retention_hours", 100_000)
    monkeypatch.setattr(settings, "event_row_cap", 500)
    db.set_setting(events_store.EVENT_POLICY_KEY, None)
    events_store.seed_defaults()

    body = client.get("/api/settings").json()
    assert body["event_policy"] == {"retention_hours": 8760, "row_cap": 1000}
    assert body["event_policy_default_limit"] == {
        "configured": {"retention_hours": 100_000, "row_cap": 500},
        "effective": {"retention_hours": 8760, "row_cap": 1000},
    }

    saved = client.put("/api/settings", json={"event_policy": body["event_policy"]}).json()
    assert saved["event_policy_default_limit"] is None
    assert client.get("/api/settings").json()["event_policy_default_limit"] is None


def test_timezone_is_stored_with_an_explicit_default(client):
    r = _put(client, {"timezone": " America/Chicago "})
    assert r.status_code == 200, r.text
    assert r.json()["retention_policy"]["timezone"] == "America/Chicago"
    assert store.retention_policy().timezone == "America/Chicago"
    body = client.get("/api/settings").json()
    assert "server_timezone" not in body
    assert _put(client, {"timezone": ""}).status_code == 400


def test_deployment_default_uses_tz_then_utc(monkeypatch):
    monkeypatch.delenv("VCF_DOCTOR_RETENTION_TIMEZONE", raising=False)
    monkeypatch.setenv("TZ", "America/Chicago")
    assert Settings().retention_timezone == "America/Chicago"
    monkeypatch.delenv("TZ")
    assert Settings().retention_timezone == "UTC"


def test_an_unknown_stored_timezone_does_not_break_a_retention_pass():
    """A zone this machine no longer knows falls back to UTC with a warning
    rather than failing every scan's retention pass."""
    assert timezones.zone("Mars/Olympus") is UTC
    assert not timezones.is_valid("Mars/Olympus")
    assert not timezones.is_valid("")


def test_an_unknown_environment_timezone_degrades_to_utc_instead_of_failing(client, monkeypatch):
    """A typo in VCF_DOCTOR_RETENTION_TIMEZONE on a fresh install must not turn
    every snapshot listing and retention pass into a validation error."""
    monkeypatch.setattr(settings, "retention_timezone", "Amercia/Chicago")
    db.set_setting(store.RETENTION_POLICY_KEY, None)

    policy = store.retention_policy()

    assert policy.timezone == "UTC"
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/snapshots").status_code == 200
