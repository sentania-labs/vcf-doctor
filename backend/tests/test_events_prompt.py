"""The assistant prompt carries an EVENTS block."""

from datetime import UTC, datetime, timedelta

from app.assistant.prompt import (
    MAX_EVENTS,
    TASK_GUIDANCE,
    build_user_message,
    evidence_counts,
)
from app.models import AssistantContext, AssistantRequest
from app.models.event import Event

T = datetime(2026, 8, 31, 6, 0, 0, tzinfo=UTC)


def _events(n: int) -> list[Event]:
    return [
        Event(
            id=f"c1:{i}",
            connection_id="c1",
            time=T - timedelta(minutes=i),
            type="VmPoweredOffEvent",
            category="user",
            message=f"web{i:02d} is powered off",
            user="administrator@vsphere.local",
            resource_id=f"vm:c1:web{i:02d}",
            resource_name=f"web{i:02d}",
            resource_type="vm",
        )
        for i in range(n)
    ]


def _request(events: list[Event]) -> AssistantRequest:
    return AssistantRequest(
        task="explain",
        context=AssistantContext(question="Who powered off web03?", events=events),
    )


def test_events_block_present_and_described():
    um = build_user_message(_request(_events(3)))
    assert "EVENTS (what vCenter recorded in the window" in um
    assert '"type":"VmPoweredOffEvent"' in um
    assert '"user":"administrator@vsphere.local"' in um
    assert "web02 is powered off" in um
    assert "No findings, changes" not in um  # events count as evidence
    assert "EVENTS" in TASK_GUIDANCE
    assert evidence_counts(_request(_events(3))) == {
        "findings": 0,
        "changes": 0,
        "resources": 0,
        "events": 3,
    }


def test_events_truncated_at_cap_with_note():
    um = build_user_message(_request(_events(MAX_EVENTS + 7)))
    assert f"first {MAX_EVENTS} of {MAX_EVENTS + 7} events" in um
    assert f"web{MAX_EVENTS + 5:02d}" not in um
    assert evidence_counts(_request(_events(MAX_EVENTS + 7)))["events"] == MAX_EVENTS + 7


def test_no_evidence_message_mentions_events():
    um = build_user_message(_request([]))
    assert "No findings, changes, resources or events were supplied" in um
    assert "EVENTS" in um and '"count":0' in um
