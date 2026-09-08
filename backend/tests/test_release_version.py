import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/check-release-version.sh"


@pytest.mark.parametrize(
    ("tag_kind", "same_commit", "allowed"),
    [
        (None, False, True),
        ("lightweight", True, True),
        ("lightweight", False, False),
        ("annotated", True, True),
        ("annotated", False, False),
        ("unavailable", False, False),
    ],
)
def test_release_version_ownership(tmp_path, tag_kind, same_commit, allowed):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init")
    git("config", "user.name", "Release test")
    git("config", "user.email", "release@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "tag.gpgsign", "false")
    git("commit", "--allow-empty", "-m", "Build A")
    build_sha = git("rev-parse", "HEAD")
    if not same_commit:
        git("commit", "--allow-empty", "-m", "Build B")
    if tag_kind == "lightweight":
        git("tag", "v0.1.68")
    elif tag_kind == "annotated":
        git("tag", "-a", "v0.1.68", "-m", "Release")
    remote = tmp_path / "missing" if tag_kind == "unavailable" else tmp_path
    git("remote", "add", "origin", str(remote))

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env={**os.environ, "VERSION": "v0.1.68", "GITHUB_SHA": build_sha},
        capture_output=True,
        text=True,
    )

    assert (result.returncode == 0) is allowed, result.stderr
    if tag_kind in {"lightweight", "annotated"} and not same_commit:
        assert "belongs to" in result.stderr
