"""Health score: severity-weighted, per-object normalised, floored at zero.

For each check: deduction = sum over its findings of weight(severity), divided
by the number of objects the check evaluated (so one bad host out of four
costs ten times what one out of forty does), capped at the largest weight.
score = 100 minus the sum of deductions, floored at 0, rounded to an integer.

Weights are operator-editable in Settings (settings KV key health_weights)
and fall back to the defaults below. VCF_DOCTOR_HEALTH_WEIGHTS may seed the
defaults at deploy time as "critical=40,warning=15,info=0".
"""

import logging
import os
from typing import Any

from app import db
from app.models import Finding

log = logging.getLogger(__name__)

SEVERITIES = ("critical", "warning", "info")
DEFAULT_WEIGHTS: dict[str, int] = {"critical": 40, "warning": 15, "info": 0}
WEIGHTS_KEY = "health_weights"
MAX_WEIGHT = 100

FORMULA = (
    "Score = 100 minus, for each check, weight(severity) times the share of the "
    "objects that check evaluated which have a finding, summed and floored at 0. "
    "A check with no applicable objects (or that needs a previous snapshot) counts "
    "as not evaluated rather than passed."
)


def _env_defaults() -> dict[str, int]:
    raw = os.environ.get("VCF_DOCTOR_HEALTH_WEIGHTS", "")
    out = dict(DEFAULT_WEIGHTS)
    if not raw.strip():
        return out
    for part in raw.split(","):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        if key not in SEVERITIES:
            continue
        try:
            out[key] = _coerce(value.strip())
        except ValueError:
            log.warning("VCF_DOCTOR_HEALTH_WEIGHTS: ignoring %s", part.strip())
    return out


def default_weights() -> dict[str, int]:
    return _env_defaults()


def _coerce(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("weight must be a whole number")
    if isinstance(value, str):
        value = value.strip()
        if not value.lstrip("-").isdigit():
            raise ValueError("weight must be a whole number")
        value = int(value)
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("weight must be a whole number")
        value = int(value)
    if not isinstance(value, int):
        raise ValueError("weight must be a whole number")
    if value < 0 or value > MAX_WEIGHT:
        raise ValueError(f"weight must be between 0 and {MAX_WEIGHT}")
    return value


def validate_weights(update: dict[str, Any], base: dict[str, int] | None = None) -> dict[str, int]:
    """Merge a partial update over base (stored weights by default). Raises
    ValueError with an operator-readable message on bad input."""
    unknown = set(update) - set(SEVERITIES)
    if unknown:
        raise ValueError(f"unknown severities: {', '.join(sorted(unknown))}")
    merged = dict(base if base is not None else get_weights())
    for key, value in update.items():
        try:
            merged[key] = _coerce(value)
        except ValueError as exc:
            raise ValueError(f"{key}: {exc}") from exc
    return merged


def get_weights() -> dict[str, int]:
    stored = db.get_setting(WEIGHTS_KEY)
    base = default_weights()
    if not isinstance(stored, dict):
        return base
    try:
        return validate_weights(stored, base)
    except ValueError:
        log.warning("stored %s is invalid; using defaults", WEIGHTS_KEY)
        return base


def set_weights(update: dict[str, Any]) -> dict[str, int]:
    merged = validate_weights(update)
    db.set_setting(WEIGHTS_KEY, merged)
    return merged


def reset_weights() -> dict[str, int]:
    with db.transaction() as c:
        c.execute("DELETE FROM settings WHERE key = ?", (WEIGHTS_KEY,))
    return default_weights()


def compute_health(
    findings: list[Finding],
    coverage: dict[str, int],
    weights: dict[str, int] | None = None,
) -> dict[str, Any]:
    """coverage: check id -> objects evaluated (0 = not evaluated). Findings from
    a check missing from coverage still count; its denominator becomes the
    number of findings so the deduction is the full weight."""
    w = dict(DEFAULT_WEIGHTS)
    w.update(weights if weights is not None else get_weights())
    cap = max(w.values()) if w else 0
    per_check: dict[str, dict[str, Any]] = {}
    for check_id, n in coverage.items():
        per_check[check_id] = {
            "check_id": check_id, "evaluated": n, "findings": 0, "deduction": 0.0
        }
    for f in findings:
        blank = {"check_id": f.check_id, "evaluated": 0, "findings": 0, "deduction": 0.0}
        entry = per_check.setdefault(f.check_id, blank)
        entry["findings"] += 1
        entry["deduction"] += float(w.get(f.severity, 0))
    total = 0.0
    passed = with_findings = not_evaluated = 0
    for entry in per_check.values():
        n = max(entry["evaluated"], entry["findings"])
        entry["evaluated"] = n
        if entry["findings"] > 0:
            entry["deduction"] = round(min(cap, entry["deduction"] / n), 2)
            with_findings += 1
        elif n == 0:
            not_evaluated += 1
        else:
            passed += 1
        total += entry["deduction"]
    score = int(round(max(0.0, 100.0 - total)))
    # What the operator sees as "lost" must add up to the score shown next to it.
    lost = 100 - score
    checks = sorted(per_check.values(), key=lambda e: (-e["deduction"], e["check_id"]))
    return {
        "score": score,
        "passed": passed,
        "findings": with_findings,
        "not_evaluated": not_evaluated,
        "deduction": lost,
        "weights": w,
        "formula": FORMULA,
        "checks": checks,
    }


__all__ = [
    "DEFAULT_WEIGHTS",
    "FORMULA",
    "compute_health",
    "default_weights",
    "get_weights",
    "reset_weights",
    "set_weights",
    "validate_weights",
]
