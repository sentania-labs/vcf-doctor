"""Check registry: the one place the rest of the app calls into for diagnostics."""

import logging

from app.diagnostics.base import DiagnosticCheck
from app.diagnostics.checks import ALL_CHECKS
from app.models import Finding, Resource

log = logging.getLogger(__name__)

_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def get_checks() -> list[DiagnosticCheck]:
    return [cls() for cls in ALL_CHECKS]


def list_checks() -> list[dict]:
    return [{"id": c.id, "name": c.name, "description": c.description} for c in get_checks()]


def run_all(resources: list[Resource], previous: list[Resource] | None = None) -> list[Finding]:
    """Run every registered check. Output is deterministic: sorted by severity
    (critical first), then check id, then resource id. One check raising does
    not stop the others."""
    findings: list[Finding] = []
    seen: set[str] = set()
    for check in get_checks():
        try:
            results = check.evaluate(resources, previous)
        except Exception:  # noqa: BLE001 - one bad check must not sink the scan
            log.exception("check %s failed; skipping", check.id)
            continue
        for f in results:
            if f.id in seen:
                continue
            seen.add(f.id)
            findings.append(f)
    findings.sort(
        key=lambda f: (_SEVERITY_RANK.get(f.severity, 9), f.check_id, f.resource_id or "")
    )
    return findings


__all__ = ["get_checks", "list_checks", "run_all"]
