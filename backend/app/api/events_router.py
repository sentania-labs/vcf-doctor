"""GET /api/events: what vCenter recorded, newest first."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.events import store as events_store
from app.models.event import Event
from app.snapshots import store

router = APIRouter(prefix="/api/events", tags=["events"])

DEFAULT_WINDOW = timedelta(hours=24)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@router.get("", response_model=list[Event])
@router.get("/", response_model=list[Event], include_in_schema=False)
def list_events(
    connection_id: Annotated[str | None, Query(description="Scope to one connection")] = None,
    since: Annotated[datetime | None, Query(description="ISO 8601; default until - 24 h")] = None,
    until: Annotated[datetime | None, Query(description="ISO 8601; default open")] = None,
    resource_id: Annotated[str | None, Query(description="Exact resource id")] = None,
    category: Annotated[str | None, Query(description="info | warning | error | user")] = None,
    q: Annotated[str | None, Query(description="Match on message, user or name")] = None,
    limit: Annotated[int, Query(ge=1, le=events_store.MAX_LIMIT)] = events_store.DEFAULT_LIMIT,
) -> list[Event]:
    if connection_id and store.get_connection(connection_id) is None:
        raise HTTPException(404, f"connection {connection_id} not found")
    if category and category not in events_store.CATEGORIES:
        raise HTTPException(422, f"category must be one of {', '.join(events_store.CATEGORIES)}")
    since_dt, until_dt = _aware(since), _aware(until)
    if since_dt is None:
        since_dt = (until_dt or datetime.now(UTC)) - DEFAULT_WINDOW
    return events_store.list_events(
        connection_id=connection_id,
        since=since_dt,
        until=until_dt,
        resource_id=resource_id,
        category=category,
        q=q,
        limit=limit,
    )
