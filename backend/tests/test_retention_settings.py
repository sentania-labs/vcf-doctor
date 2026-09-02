"""retention_policy on /api/settings: validation, partial merge, legacy count ignored."""

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


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
    assert body["retention_policy"] == {"recent_days": 14, "hourly_days": 30, "daily_days": 365}
    assert "retention" not in body


def test_partial_update_merges_and_persists(client):
    r = _put(client, {"hourly_days": 60, "daily_days": 400})
    assert r.status_code == 200, r.text
    assert r.json()["retention_policy"] == {"recent_days": 14, "hourly_days": 60, "daily_days": 400}
    assert db.get_setting("retention_policy") == {
        "recent_days": 14,
        "hourly_days": 60,
        "daily_days": 400,
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
