"""retention_policy on /api/settings: validation, partial merge, legacy count ignored."""

from datetime import UTC

import pytest
from fastapi.testclient import TestClient

from app import db, timezones
from app.main import app
from app.snapshots import store


@pytest.fixture()
def client(tmp_path):
    db.reset_for_tests(str(tmp_path / "t.db"))
    with TestClient(app) as c:
        yield c


def _put(client, policy):
    return client.put("/api/settings", json={"retention_policy": policy})


def test_defaults_come_from_config_and_old_count_is_ignored(client):
    db.set_setting("retention", 2)  # a pre-tier database that only has the old count
    body = client.get("/api/settings").json()
    assert body["retention_policy"] == {
        "recent_days": 14,
        "hourly_days": 30,
        "daily_days": 365,
        "timezone": "",  # empty: day marks follow the server's own zone
    }
    assert body["server_timezone"]  # the GUI names the default it is offering
    assert body["event_policy"] == {"retention_hours": 48, "row_cap": 250000}
    assert body["event_maintenance"]["last_run"] is None
    assert db.get_setting("event_policy") == {"retention_hours": 48, "row_cap": 250000}
    assert "retention" not in body


def test_partial_update_merges_and_persists(client):
    r = _put(client, {"hourly_days": 60, "daily_days": 400})
    assert r.status_code == 200, r.text
    assert r.json()["retention_policy"] == {
        "recent_days": 14,
        "hourly_days": 60,
        "daily_days": 400,
        "timezone": "",
    }
    assert db.get_setting("retention_policy") == {
        "recent_days": 14,
        "hourly_days": 60,
        "daily_days": 400,
        "timezone": "",
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


def test_timezone_is_stored_and_shown_with_the_server_default(client):
    """Issue #28: the day tier anchors at local midnight, and the zone is a
    Settings value with a working default (empty: follow the server)."""
    r = _put(client, {"timezone": " America/Chicago "})
    assert r.status_code == 200, r.text
    assert r.json()["retention_policy"]["timezone"] == "America/Chicago"
    assert store.retention_policy().timezone == "America/Chicago"
    # Back to the default, which is whatever the server itself is set to.
    assert _put(client, {"timezone": ""}).json()["retention_policy"]["timezone"] == ""
    body = client.get("/api/settings").json()
    assert body["server_timezone"] == timezones.server_timezone()
    assert timezones.zone("") is timezones.zone(timezones.server_timezone())


def test_an_unknown_stored_timezone_does_not_break_a_retention_pass():
    """A zone this machine no longer knows falls back to UTC with a warning
    rather than failing every scan's retention pass."""
    assert timezones.zone("Mars/Olympus") is UTC
    assert not timezones.is_valid("Mars/Olympus")
    assert timezones.is_valid("")
