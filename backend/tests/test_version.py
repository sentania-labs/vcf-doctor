import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app._version import BUILD_INFO, load_build_info
from app.main import app


@pytest.fixture()
def client(tmp_path):
    db.reset_for_tests()
    with TestClient(app) as test_client:
        yield test_client


def test_version_endpoint_and_health_share_build_identity(client):
    identity = client.get("/api/version")
    assert identity.status_code == 200
    assert identity.json() == BUILD_INFO.as_dict()
    assert set(identity.json()) == {"version", "sha", "built_at"}
    assert client.get("/api/health").json()["version"] == identity.json()["version"]
    assert app.version == identity.json()["version"]


def test_development_fallback_uses_checkout_sha(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    expected_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    identity = load_build_info(tmp_path / "missing-version-file", git_cwd=repo)

    assert identity.version == "dev"
    assert identity.sha == expected_sha
    assert identity.built_at == "unknown"
