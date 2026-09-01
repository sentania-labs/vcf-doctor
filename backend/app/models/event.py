from datetime import datetime
from typing import Literal

from pydantic import BaseModel

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
