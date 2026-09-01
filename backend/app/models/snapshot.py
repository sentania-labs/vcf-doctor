from datetime import datetime

from pydantic import BaseModel, Field

from app.models.resource import Resource


class SnapshotSummary(BaseModel):
    id: str
    created_at: datetime
    label: str
    connection_id: str
    scheduled: bool = False  # True when produced by the scheduler; eligible for pruning
    resource_count: int = 0


class Snapshot(SnapshotSummary):
    resources: list[Resource] = Field(default_factory=list)
