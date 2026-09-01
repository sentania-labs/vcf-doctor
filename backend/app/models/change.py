from typing import Any, Literal

from pydantic import BaseModel, Field

Significance = Literal["low", "medium", "high"]


class PropertyChange(BaseModel):
    old: Any = None
    new: Any = None


class Change(BaseModel):
    change_type: Literal["added", "removed", "modified"]
    resource_id: str
    resource_type: str
    resource_name: str
    property_changes: dict[str, PropertyChange] = Field(default_factory=dict)
    significance: Significance
    summary: str = ""  # human sentence, e.g. "connected -> disconnected"
