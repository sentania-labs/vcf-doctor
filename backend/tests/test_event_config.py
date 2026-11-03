"""Event environment defaults are validated before the application can serve."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.config import settings as cfg
from app.events import store
from app.main import app


@pytest.mark.parametrize(
    ("variable", "values", "allowed"),
    [
        ("VCF_DOCTOR_EVENT_RETENTION_HOURS", ["0", "8761", "many", "1.5", ""], "1..8760"),
        ("VCF_DOCTOR_EVENT_ROW_CAP", ["100", "10000001", "many", "1000.5", ""], "1000..10000000"),
    ],
)
def test_startup_refuses_invalid_event_environment(variable, values, allowed):
    for value in values:
        env = os.environ.copy()
        env.pop("VCF_DOCTOR_EVENT_RETENTION_HOURS", None)
        env.pop("VCF_DOCTOR_EVENT_ROW_CAP", None)
        env[variable] = value
        result = subprocess.run(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "0"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode != 0
        assert (
            f"{variable}={value!r} must be an integer in the range {allowed} (inclusive)"
            in result.stderr
        ), result.stderr


@pytest.mark.parametrize("hours,cap", [(1, 1000), (8760, 10_000_000), (42, 100_000)])
def test_settings_serves_validated_environment_defaults_unchanged(monkeypatch, hours, cap):
    db.reset_for_tests()
    monkeypatch.setenv("VCF_DOCTOR_EVENT_RETENTION_HOURS", str(hours))
    monkeypatch.setenv("VCF_DOCTOR_EVENT_ROW_CAP", str(cap))
    validated = Settings()
    assert validated.event_retention_hours == hours
    assert validated.event_row_cap == cap
    monkeypatch.setattr(cfg, "event_retention_hours", validated.event_retention_hours)
    monkeypatch.setattr(cfg, "event_row_cap", validated.event_row_cap)
    # Once read, the environment is not consulted by the store or Settings API.
    monkeypatch.setenv("VCF_DOCTOR_EVENT_ROW_CAP", "100")
    expected = {"retention_hours": hours, "row_cap": cap}
    assert store.default_event_policy().model_dump() == expected
    # Exercise both the unsaved fallback and the policy seeded at startup.
    with TestClient(app) as client:
        assert store.event_policy().model_dump() == expected
        response = client.get("/api/settings")
        assert response.status_code == 200
        assert response.json()["event_policy"] == expected
        with db.transaction() as connection:
            connection.execute("DELETE FROM settings WHERE key = %s", (store.EVENT_POLICY_KEY,))
        response = client.get("/api/settings")
        assert response.status_code == 200
        assert response.json()["event_policy"] == expected


def test_event_environment_defaults_work_when_unset(monkeypatch):
    monkeypatch.delenv("VCF_DOCTOR_EVENT_RETENTION_HOURS", raising=False)
    monkeypatch.delenv("VCF_DOCTOR_EVENT_ROW_CAP", raising=False)
    validated = Settings()
    assert validated.event_retention_hours == 48
    assert validated.event_row_cap == 250_000
