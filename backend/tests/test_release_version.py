import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/check-release-version.sh"


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def release_repository(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Release test")
    _git(tmp_path, "config", "user.email", "release@example.invalid")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _git(tmp_path, "config", "tag.gpgsign", "false")

    _git(tmp_path, "commit", "--allow-empty", "-m", "Release commit")
    release_sha = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "tag", "v1.2.3")
    _git(tmp_path, "tag", "-a", "v1.2.4", "-m", "Annotated release")

    _git(tmp_path, "commit", "--allow-empty", "-m", "Later main commit")
    main_sha = _git(tmp_path, "rev-parse", "HEAD")

    _git(tmp_path, "checkout", "-b", "stray")
    _git(tmp_path, "commit", "--allow-empty", "-m", "Unmerged commit")
    stray_sha = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "tag", "v1.2.5")
    _git(tmp_path, "checkout", "main")
    _git(tmp_path, "remote", "add", "origin", str(tmp_path))

    return tmp_path, release_sha, main_sha, stray_sha


def _check(repository: Path, version: str, build_sha: str):
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=repository,
        env={
            **os.environ,
            "VERSION": version,
            "GITHUB_SHA": build_sha,
            "MAINLINE_REF": "main",
        },
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("version", ["v1.2.3", "v1.2.4"])
def test_release_tag_on_main_is_allowed(release_repository, version):
    repository, release_sha, _, _ = release_repository

    result = _check(repository, version, release_sha)

    assert result.returncode == 0, result.stderr
    assert "is on main" in result.stdout


def test_release_tag_owned_by_another_commit_is_refused(release_repository):
    repository, _, main_sha, _ = release_repository

    result = _check(repository, "v1.2.3", main_sha)

    assert result.returncode == 1
    assert "belongs to" in result.stderr


def test_release_tag_outside_main_is_refused(release_repository):
    repository, _, _, stray_sha = release_repository

    result = _check(repository, "v1.2.5", stray_sha)

    assert result.returncode == 1
    assert "not reachable from main" in result.stderr
    assert "merge the change to main first" in result.stderr


@pytest.mark.parametrize("version", ["v1.2", "release-1.2.3", "v1.2.3-rc1"])
def test_malformed_release_tag_is_refused(release_repository, version):
    repository, release_sha, _, _ = release_repository

    result = _check(repository, version, release_sha)

    assert result.returncode == 2
    assert "must match vMAJOR.MINOR.PATCH" in result.stderr


def test_missing_release_tag_is_refused(release_repository):
    repository, release_sha, _, _ = release_repository

    result = _check(repository, "v9.9.9", release_sha)

    assert result.returncode == 2
    assert "does not exist on origin" in result.stderr
