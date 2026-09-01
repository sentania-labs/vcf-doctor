import asyncio

from app.assistant.prompt import INVESTIGATION_HEADING, MODIFICATION_HEADING
from app.assistant.providers import MockProvider
from app.models import AssistantContext, AssistantRequest, Change, Finding, Resource


def _request(task="explain", script_format=None) -> AssistantRequest:
    return AssistantRequest(
        task=task,
        script_format=script_format,
        context=AssistantContext(
            question="What happened to esx03?",
            findings=[
                Finding(
                    id="f1",
                    check_id="HOST_DISCONNECTED",
                    severity="critical",
                    title="Host disconnected",
                    summary="esx03 is disconnected",
                    resource_name="esx03",
                    resource_type="host",
                    recommendation="Reconnect after verifying reachability",
                )
            ],
            changes=[
                Change(
                    change_type="modified",
                    resource_id="host:vc01:esx03",
                    resource_type="host",
                    resource_name="esx03",
                    significance="high",
                    summary="connected -> disconnected",
                )
            ],
            resources=[
                Resource(id="host:vc01:esx03", type="host", name="esx03", source="vcenter:vc01"),
                Resource(id="ds:vc01:ds01", type="datastore", name="ds01", source="vcenter:vc01"),
            ],
        ),
    )


async def _collect(provider, request):
    return [c async for c in provider.stream(request)]


def test_mock_streams_in_chunks_and_mentions_evidence():
    p = MockProvider()
    chunks = asyncio.run(_collect(p, _request()))
    assert len(chunks) > 3
    text = "".join(chunks)
    assert "Host disconnected" in text
    assert "esx03" in text and "ds01" in text
    assert "connected -> disconnected" in text
    assert "## Observed facts" in text and "## Suggested remediation" in text
    assert p.last_stop_reason == "end_turn"


def test_mock_status_available():
    st = asyncio.run(MockProvider().status())
    assert st.available and st.provider == "mock"


def test_mock_script_has_two_sections_for_every_format():
    for fmt in ("powercli", "python", "shell", "rest"):
        text = "".join(asyncio.run(_collect(MockProvider(), _request("generate-script", fmt))))
        assert INVESTIGATION_HEADING in text, fmt
        assert MODIFICATION_HEADING in text, fmt
        assert text.index(INVESTIGATION_HEADING) < text.index(MODIFICATION_HEADING)
        assert text.count("```") == 4, fmt
        assert "esx03" in text
        assert "Nothing in this response is executed by VCF Doctor" in text
