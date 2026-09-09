import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/promote-latest.sh"


@pytest.fixture
def registry(tmp_path):
    executable = tmp_path / "skopeo"
    executable.write_text(
        """#!/usr/bin/env python3
import hashlib
import json
import os
import sys
from pathlib import Path

path = Path(os.environ["REGISTRY"])
state = json.loads(path.read_text())
args = sys.argv[1:]
if os.environ.get("FAIL") == args[0]:
    sys.exit(1)
if args[0] == "list-tags":
    print(json.dumps({"Tags": list(state)}))
elif args[0] == "inspect":
    sys.stdout.write(state[args[-1].rsplit(":", 1)[1]])
elif args[0] == "copy":
    digest = args[-2].split("@sha256:")[1]
    manifest = next(value for value in state.values()
                    if hashlib.sha256(value.encode()).hexdigest() == digest)
    state["latest"] = "corrupt" if os.environ.get("CORRUPT") else manifest
    path.write_text(json.dumps(state))
else:
    sys.exit(2)
"""
    )
    executable.chmod(0o755)
    state = tmp_path / "registry.json"
    state.write_text("{}")
    return state, {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "REGISTRY": str(state),
        "IMAGE": "registry.example/product",
    }


def _promote(registry, **environment):
    _, env = registry
    return subprocess.run(
        ["bash", str(SCRIPT)], env={**env, **environment}, capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "versions",
    [
        ["v1.2.9", "v1.3.0"],
        ["v1.3.0", "v1.2.9"],
        ["v1.9.0", "v1.10.0", "v1.2.9"],
    ],
)
def test_out_of_order_publication_and_retries_keep_highest_version(registry, versions):
    state, _ = registry
    expected = ""
    for version in versions:
        published = json.loads(state.read_text())
        published[version] = json.dumps({"version": version})
        published["v99.0.0-rc1"] = "prerelease"
        published["sha-abcdef0"] = "main build"
        state.write_text(json.dumps(published))
        expected = max(
            versions[:versions.index(version) + 1],
            key=lambda v: tuple(map(int, v[1:].split("."))),
        )
        result = _promote(registry)
        assert result.returncode == 0, result.stderr
        assert json.loads(state.read_text())["latest"] == published[expected]

    result = _promote(registry)
    assert result.returncode == 0, result.stderr
    assert json.loads(json.loads(state.read_text())["latest"])["version"] == expected


def test_replaced_pending_promotion_reconciles_all_published_versions(registry):
    state, _ = registry
    state.write_text(json.dumps({"v1.3.0": "newest", "v1.2.9": "older"}))

    result = _promote(registry)

    assert result.returncode == 0, result.stderr
    assert json.loads(state.read_text())["latest"] == "newest"


@pytest.mark.parametrize("failure", ["list-tags", "inspect", "copy"])
def test_registry_failure_does_not_move_latest(registry, failure):
    state, _ = registry
    published = {"v1.3.0": "newest", "latest": "previous"}
    state.write_text(json.dumps(published))

    result = _promote(registry, FAIL=failure)

    assert result.returncode != 0
    assert json.loads(state.read_text()) == published


def test_digest_mismatch_fails_promotion(registry):
    state, _ = registry
    state.write_text(json.dumps({"v1.3.0": "newest"}))

    result = _promote(registry, CORRUPT="1")

    assert result.returncode != 0
    assert "expected v1.3.0 digest" in result.stderr


def test_no_published_release_fails_without_moving_latest(registry):
    state, _ = registry
    state.write_text(json.dumps({"latest": "previous"}))

    result = _promote(registry)

    assert result.returncode != 0
    assert json.loads(state.read_text()) == {"latest": "previous"}
