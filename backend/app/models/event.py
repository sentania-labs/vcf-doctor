from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

EventSource = Literal["event", "task"]
EventCategory = Literal["info", "warning", "error", "user"]


class Event(BaseModel):
    """One thing vCenter recorded (an event or a task), normalized.

    id is "<connection_id>:<vc event key>" for events and
    "<connection_id>:task:<task key>" for tasks, so a window that is fetched
    twice (the 60 s overlap between scans) deduplicates in the store.
    resource_id follows the snapshot id scheme ("<type>:<connection_id>:<moref>")
    so an event can be joined to the resource it happened to.
    """

    id: str
    connection_id: str
    time: datetime
    source: EventSource = "event"
    type: str
    category: EventCategory = "info"
    message: str = ""
    user: str | None = None
    resource_id: str | None = None
    resource_name: str | None = None
    resource_type: str | None = None


class EventPolicy(BaseModel):
    retention_hours: int = Field(ge=1, le=8760)
    row_cap: int = Field(ge=1000, le=10_000_000)


class IncompleteInterval(BaseModel):
    id: int
    connection_id: str
    since: datetime
    until: datetime
    attempts: int
    last_error: str | None = None
    updated_at: datetime


class EventCaptureStatus(BaseModel):
    connection_id: str
    last_complete_end: datetime | None = None
    task_history_unavailable: bool = False
    incomplete_intervals: list[IncompleteInterval] = Field(default_factory=list)


class EventMaintenanceStatus(BaseModel):
    migration_required: bool = False
    last_run: datetime | None = None
    last_error: str | None = None
    pages_reclaimed: int = 0
