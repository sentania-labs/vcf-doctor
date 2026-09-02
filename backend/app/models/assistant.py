from typing import Literal

from pydantic import BaseModel, Field

from app.models.change import Change
from app.models.event import Event
from app.models.finding import Finding
from app.models.resource import Resource

AssistantTask = Literal["explain", "investigate", "generate-script", "ask"]
ScriptFormat = Literal["powercli", "python", "shell", "rest"]


class AssistantContext(BaseModel):
    """Evidence package. The model may interpret this and nothing else."""

    question: str
    findings: list[Finding] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)  # what vCenter recorded in the window
    allowed_actions: list[str] = Field(default_factory=lambda: ["read-only"])


class AssistantRequest(BaseModel):
    task: AssistantTask = "ask"
    script_format: ScriptFormat | None = None
    context: AssistantContext


class AssistantSettings(BaseModel):
    enabled: bool = True
    provider: Literal["anthropic", "mock"] = "anthropic"
    model: str = "claude-opus-5"
    api_key_set: bool = False  # never the key itself


class AssistantStatus(BaseModel):
    available: bool
    provider: str
    model: str
    reason: str | None = None  # why unavailable, human readable
