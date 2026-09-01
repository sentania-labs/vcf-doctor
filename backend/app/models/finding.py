from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["info", "warning", "critical"]


class Finding(BaseModel):
    id: str
    check_id: str
    severity: Severity
    title: str
    summary: str
    resource_id: str | None = None
    resource_name: str | None = None
    resource_type: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    recommendation: str | None = None
