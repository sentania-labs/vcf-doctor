from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ScanStatus = Literal["running", "ok", "error", "skipped"]


class ConnectionCreate(BaseModel):
    name: str
    host: str
    username: str
    password: str
    verify_tls: bool = False
    interval_minutes: int = 15
    enabled: bool = True
    # "vcenter" (live). "fixture" (bundled test data) is accepted only with
    # the VCF_DOCTOR_TEST_FIXTURES hook; see api/router._check_kind.
    kind: str = "vcenter"


class Connection(ConnectionCreate):
    id: str
    created_at: datetime
    # True when the stored password is encrypted under a key this process
    # does not have (key lost or rotated). password is "" in that case.
    credentials_unreadable: bool = False


class ConnectionPublic(BaseModel):
    """What the API returns. Never includes the password."""

    id: str
    name: str
    host: str
    username: str
    verify_tls: bool
    created_at: datetime
    kind: str = "vcenter"
    # The operator must re-enter the password before this connection can scan.
    needs_credentials: bool = False


class ConnectionResult(BaseModel):
    ok: bool
    message: str
    version: str | None = None
    build: str | None = None


class Schedule(BaseModel):
    connection_id: str
    interval_minutes: int = 15
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None
    last_status: ScanStatus | None = None


class ScheduleUpdate(BaseModel):
    interval_minutes: int | None = Field(default=None, ge=1)
    enabled: bool | None = None


class ScanRun(BaseModel):
    id: str
    connection_id: str
    started: datetime
    finished: datetime | None = None
    status: ScanStatus
    error: str | None = None
    snapshot_id: str | None = None
    trigger: Literal["scheduled", "manual"] = "manual"
