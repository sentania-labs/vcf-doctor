"""Health score weights: GUI-editable in Settings, with a reset to defaults."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.diagnostics import scoring

router = APIRouter(prefix="/api/settings/health-score", tags=["settings"])


class HealthScoreSettings(BaseModel):
    weights: dict[str, int]
    defaults: dict[str, int]
    formula: str


def _current() -> HealthScoreSettings:
    return HealthScoreSettings(
        weights=scoring.get_weights(), defaults=scoring.default_weights(), formula=scoring.FORMULA
    )


@router.get("", response_model=HealthScoreSettings)
def get_health_score_settings():
    return _current()


@router.put("", response_model=HealthScoreSettings)
def put_health_score_settings(body: dict[str, Any]):
    """Body: {"weights": {"critical": 40, "warning": 15, "info": 0}}; partial is fine."""
    weights = body.get("weights")
    if not isinstance(weights, dict):
        raise HTTPException(400, "weights must be an object of severity to whole-number weight")
    try:
        scoring.set_weights(weights)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _current()


@router.post("/reset", response_model=HealthScoreSettings)
def reset_health_score_settings():
    scoring.reset_weights()
    return _current()
