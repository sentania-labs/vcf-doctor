from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app import timezones
from app.models.resource import Resource

Tier = Literal["manual", "recent", "hourly", "daily"]


class RetentionPolicy(BaseModel):
    """Tiered retention for scheduled snapshots, in days of age.

    age < recent_days: keep everything; < hourly_days: one per hour;
    < daily_days: one per day; older: prune. Manual snapshots are never pruned.

    timezone is the IANA zone whose midnights are the day marks.
    Empty means the server's own zone; browser display dates are independent.
    """

    recent_days: int = Field(ge=1)
    hourly_days: int = Field(ge=1)
    daily_days: int = Field(ge=1)
    timezone: str = ""

    # Self, not RetentionPolicy: on Python 3.14.3 pydantic resolves a validator's
    # signature while the class body is still running, and the bare class name is
    # not bound yet (every test collection failed with a NameError).
    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.recent_days <= self.hourly_days <= self.daily_days:
            raise ValueError("retention tiers must satisfy recent <= hourly <= daily days")
        if not timezones.is_valid(self.timezone):
            raise ValueError(f"unknown timezone {self.timezone!r}")
        return self


class SnapshotSummary(BaseModel):
    id: str
    created_at: datetime
    label: str
    connection_id: str
    scheduled: bool = False  # True when produced by the scheduler; eligible for pruning
    resource_count: int = 0
    tier: Tier = "recent"  # computed on read from age and the retention policy
    retention_day: str = ""


class Snapshot(SnapshotSummary):
    resources: list[Resource] = Field(default_factory=list)
