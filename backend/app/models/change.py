from datetime import datetime
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


class ChangeRecord(Change):
    """A Change persisted by a scan: diff(previous, current) at scan time.

    Snapshot ids may point at snapshots since pruned; the record outlives them.
    """

    id: str
    connection_id: str
    from_snapshot_id: str
    to_snapshot_id: str
    observed_at: datetime  # the "to" snapshot's created_at
