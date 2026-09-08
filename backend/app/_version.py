"""Build identity loaded from the image, with a useful checkout fallback."""

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuildInfo:
    version: str
    sha: str
    built_at: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _checkout_sha(cwd: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unknown"


def load_build_info(
    version_path: Path = Path("/app/VERSION"),
    git_cwd: Path | None = None,
) -> BuildInfo:
    """Read immutable image metadata or describe the current development checkout."""
    try:
        raw = json.loads(version_path.read_text())
        return BuildInfo(
            version=str(raw["version"]),
            sha=str(raw["sha"]),
            built_at=str(raw["built_at"]),
        )
    except (OSError, KeyError, TypeError, ValueError):
        cwd = git_cwd or Path(__file__).resolve().parents[2]
        return BuildInfo(
            version="dev",
            sha=_checkout_sha(cwd),
            built_at="unknown",
        )


BUILD_INFO = load_build_info()
