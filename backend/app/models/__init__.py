"""Shared contracts. See docs/PLAN.md Phase 0 and docs/ORIGINAL_PLAN.md section 7.

These are frozen for Phase 1. Do not change field names or types without a
compelling reason and a note in the PR body.
"""

from app.models.assistant import (
    AssistantContext,
    AssistantRequest,
    AssistantSettings,
    AssistantStatus,
    AssistantTask,
    ScriptFormat,
)
from app.models.change import Change, Significance
from app.models.connection import (
    Connection,
    ConnectionCreate,
    ConnectionPublic,
    ConnectionResult,
    ScanRun,
    ScanStatus,
    Schedule,
    ScheduleUpdate,
)
from app.models.event import Event, EventCategory, EventSource
from app.models.finding import Finding, Severity
from app.models.resource import Relationship, Resource
from app.models.snapshot import Snapshot, SnapshotSummary

__all__ = [
    "AssistantContext",
    "AssistantRequest",
    "AssistantSettings",
    "AssistantStatus",
    "AssistantTask",
    "Change",
    "Connection",
    "ConnectionCreate",
    "ConnectionPublic",
    "ConnectionResult",
    "Event",
    "EventCategory",
    "EventSource",
    "Finding",
    "Relationship",
    "Resource",
    "ScanRun",
    "ScanStatus",
    "Schedule",
    "ScheduleUpdate",
    "ScriptFormat",
    "Severity",
    "Significance",
    "Snapshot",
    "SnapshotSummary",
]
