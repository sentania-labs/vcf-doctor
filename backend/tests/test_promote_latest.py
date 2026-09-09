import hashlib
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
if args[0] == "inspect":
    sys.stdout.write(state[args[-1].rsplit(":", 1)[1]])
elif args[0] == "copy":
    if args[-2].startswith("oci-archive:"):
        manifest = Path(args[-2].removeprefix("oci-archive:")).read_text()
    else:
        digest = args[-2].split("@sha256:")[1]
        manifest = next(value for value in state.values()
                        if hashlib.sha256(value.encode()).hexdigest() == digest)
    tag = args[-1].rsplit(":", 1)[1]
    state[tag] = "corrupt" if os.environ.get("CORRUPT") else manifest
    path.write_text(json.dumps(state))
else:
    sys.exit(2)
"""
    )
    executable.chmod(0o755)
    github = tmp_path / "gh"
    github.write_text(
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

assert sys.argv[1:] == ["api", "--paginate", "repos/example/product/releases?per_page=100"]
if os.environ.get("FAIL") == "releases":
    sys.exit(1)
sys.stdout.write(Path(os.environ["RELEASES"]).read_text())
"""
    )
    github.chmod(0o755)
    releases = tmp_path / "releases.json"
    releases.write_text("[]")
    state = tmp_path / "registry.json"
    state.write_text("{}")
    return state, {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "REGISTRY": str(state),
        "RELEASES": str(releases),
        "GITHUB_REPOSITORY": "example/product",
        "IMAGE": "registry.example/product",
    }


def _complete(registry, version, manifest, **fields):
    _, env = registry
    path = Path(env["RELEASES"])
    releases = json.loads(path.read_text())
    digest = "sha256:" + hashlib.sha256(manifest.encode()).hexdigest()
    releases.append(
        {
            "tag_name": version,
            "draft": False,
            "prerelease": False,
            "body": (
                f"Digest: {digest} (the digest scanned and smoke-tested in this run; "
                "cosign keyless signed, SBOM + provenance attached)"
            ),
            **fields,
        }
    )
    path.write_text(json.dumps(releases))


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
        _complete(registry, version, published[version])
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
    _complete(registry, "v1.3.0", "newest")
    _complete(registry, "v1.2.9", "older")

    result = _promote(registry)

    assert result.returncode == 0, result.stderr
    assert json.loads(state.read_text())["latest"] == "newest"


@pytest.mark.parametrize("failure", ["releases", "copy"])
def test_release_lookup_or_copy_failure_does_not_move_latest(registry, failure):
    state, _ = registry
    published = {"v1.3.0": "newest", "latest": "previous"}
    state.write_text(json.dumps(published))
    _complete(registry, "v1.3.0", "newest")

    result = _promote(registry, FAIL=failure)

    assert result.returncode != 0
    assert json.loads(state.read_text()) == published


def test_digest_mismatch_fails_promotion(registry):
    state, _ = registry
    state.write_text(json.dumps({"v1.3.0": "newest"}))
    _complete(registry, "v1.3.0", "newest")

    result = _promote(registry, CORRUPT="1")

    assert result.returncode != 0
    assert "expected v1.3.0 digest" in result.stderr


def test_no_published_release_fails_without_moving_latest(registry):
    state, _ = registry
    state.write_text(json.dumps({"latest": "previous"}))

    result = _promote(registry)

    assert result.returncode != 0
    assert json.loads(state.read_text()) == {"latest": "previous"}


@pytest.mark.parametrize("incomplete", ["absent", "draft", "prerelease", "no_digest"])
def test_incomplete_higher_release_cannot_be_promoted(registry, incomplete):
    state, _ = registry
    state.write_text(json.dumps({"v1.2.9": "signed", "v1.3.0": "unsigned"}))
    _complete(registry, "v1.2.9", "signed")
    if incomplete != "absent":
        fields = {"body": "No completed artifact"} if incomplete == "no_digest" else {
            incomplete: True
        }
        _complete(registry, "v1.3.0", "unsigned", **fields)

    for _ in range(2):
        result = _promote(registry)
        assert result.returncode == 0, result.stderr
        assert json.loads(state.read_text())["latest"] == "signed"

    _complete(registry, "v1.3.0", "unsigned")
    result = _promote(registry)
    assert result.returncode == 0, result.stderr
    assert json.loads(state.read_text())["latest"] == "unsigned"


def test_promotion_rejects_overwritten_version_tag(registry):
    state, _ = registry
    state.write_text(json.dumps({"v1.3.0": "unsigned retry", "retained": "signed original"}))
    _complete(registry, "v1.3.0", "signed original")

    result = _promote(registry)

    assert result.returncode != 0
    assert "completed release records" in result.stderr
    assert "latest" not in json.loads(state.read_text())


def test_paginated_release_records_preserve_version_order(registry):
    state, env = registry
    state.write_text(json.dumps({"v1.3.0": "newest", "v1.2.9": "older"}))
    _complete(registry, "v1.2.9", "older")
    _complete(registry, "v1.3.0", "newest")
    path = Path(env["RELEASES"])
    records = json.loads(path.read_text())
    path.write_text("\n".join(json.dumps([record]) for record in records))

    result = _promote(registry)

    assert result.returncode == 0, result.stderr
    assert json.loads(state.read_text())["latest"] == "newest"


def test_registry_verification_failure_fails_promotion(registry):
    state, _ = registry
    state.write_text(json.dumps({"v1.3.0": "signed"}))
    _complete(registry, "v1.3.0", "signed")

    result = _promote(registry, FAIL="inspect")

    assert result.returncode != 0


def _publish(registry, **environment):
    state, env = registry
    archive = state.parent / "image.tar"
    archive.write_text("rebuilt with new BUILD_DATE")
    output = state.parent / "output"
    output.write_text("")
    result = subprocess.run(
        ["bash", str(SCRIPT.with_name("publish-tested-image.sh"))],
        env={
            **env,
            "GITHUB_REF": "refs/tags/v1.3.0",
            "GITHUB_SHA": "abcdef0123456789",
            "VERSION": "v1.3.0",
            "TESTED": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
            "GITHUB_OUTPUT": str(output),
            "RUNNER_TEMP": str(state.parent),
            **environment,
        },
        capture_output=True,
        text=True,
    )
    return result, dict(line.split("=", 1) for line in output.read_text().splitlines())


def test_full_rerun_preserves_completed_version_and_promotes_same_digest(registry):
    state, _ = registry
    state.write_text(json.dumps({"v1.3.0": "signed original"}))
    _complete(registry, "v1.3.0", "signed original")

    for _ in range(2):
        result, output = _publish(registry)
        assert result.returncode == 0, result.stderr
        assert output == {
            "reused": "true",
            "digest": "sha256:" + hashlib.sha256(b"signed original").hexdigest(),
        }
        assert json.loads(state.read_text())["v1.3.0"] == "signed original"
        promotion = _promote(registry)
        assert promotion.returncode == 0, promotion.stderr
        assert json.loads(state.read_text())["latest"] == "signed original"


@pytest.mark.parametrize("failure", ["releases", "inspect", "mismatch", "missing_digest"])
def test_completed_release_failure_blocks_version_write(registry, failure):
    state, _ = registry
    original = {"v1.3.0": "original"}
    state.write_text(json.dumps(original))
    manifest = "different" if failure == "mismatch" else "original"
    fields = {"body": "No digest"} if failure == "missing_digest" else {}
    _complete(registry, "v1.3.0", manifest, **fields)

    result, output = _publish(registry, FAIL=failure)

    assert result.returncode != 0
    assert output == {}
    assert json.loads(state.read_text()) == original


@pytest.mark.parametrize("ref,tag", [
    ("refs/tags/v1.3.0", "v1.3.0"),
    ("refs/heads/main", "sha-abcdef0"),
])
def test_unreleased_build_publishes_tested_artifact(registry, ref, tag):
    state, _ = registry

    result, output = _publish(registry, GITHUB_REF=ref)

    assert result.returncode == 0, result.stderr
    assert output["reused"] == "false"
    assert json.loads(state.read_text()) == {tag: "rebuilt with new BUILD_DATE"}
    assert output["digest"] == "sha256:" + hashlib.sha256(
        b"rebuilt with new BUILD_DATE"
    ).hexdigest()
