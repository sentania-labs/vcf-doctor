from app.assistant.prompt import (
    INVESTIGATION_HEADING,
    MAX_RESOURCES,
    MODIFICATION_HEADING,
    NOT_EXECUTED_STATEMENT,
    build_system_prompt,
    build_user_message,
    evidence_counts,
)
from app.models import AssistantContext, AssistantRequest, Change, Finding, Resource


def _finding() -> Finding:
    return Finding(
        id="f1",
        check_id="HOST_DISCONNECTED",
        severity="critical",
        title="Host esx03 is disconnected",
        summary="connectionState changed from connected to disconnected",
        resource_id="host:vc01:esx03",
        resource_name="esx03",
        resource_type="host",
        evidence={"connectionState": "disconnected"},
        recommendation="Check management network reachability",
    )


def _change() -> Change:
    return Change(
        change_type="modified",
        resource_id="host:vc01:esx03",
        resource_type="host",
        resource_name="esx03",
        significance="high",
        summary="connected -> disconnected",
    )


def _request(task="explain", resources=None, script_format=None) -> AssistantRequest:
    return AssistantRequest(
        task=task,
        script_format=script_format,
        context=AssistantContext(
            question="Why is esx03 disconnected?",
            findings=[_finding()],
            changes=[_change()],
            resources=resources
            or [Resource(id="host:vc01:esx03", type="host", name="esx03", source="vcenter:vc01")],
        ),
    )


def test_system_prompt_contains_prohibition_and_categories():
    sp = build_system_prompt(_request())
    assert "You are the VCF Doctor assistant." in sp
    assert "Do not invent VMware resources" in sp
    assert "1. Observed facts" in sp and "4. Suggested remediation" in sp
    assert "Only make factual statements about the environment" in sp


def test_user_message_contains_evidence():
    um = build_user_message(_request())
    assert "Task: explain" in um
    assert "Why is esx03 disconnected?" in um
    assert "HOST_DISCONNECTED" in um
    assert "connected -> disconnected" in um
    assert '"name":"esx03"' in um
    assert "Only make factual statements" in um


def test_generate_script_requires_sections_and_format():
    sp = build_system_prompt(_request(task="generate-script", script_format="python"))
    assert INVESTIGATION_HEADING in sp
    assert MODIFICATION_HEADING in sp
    assert "Python" in sp
    assert NOT_EXECUTED_STATEMENT in sp
    um = build_user_message(_request(task="generate-script", script_format="python"))
    assert "Task: generate-script" in um


def test_resources_truncated_with_note():
    many = [
        Resource(id=f"vm:vc01:vm{i}", type="vm", name=f"vm{i}", source="vcenter:vc01")
        for i in range(MAX_RESOURCES + 15)
    ]
    um = build_user_message(_request(resources=many))
    assert "truncated" in um
    assert f"first {MAX_RESOURCES} of {MAX_RESOURCES + 15} resources" in um
    assert f"vm{MAX_RESOURCES + 10}" not in um
    assert evidence_counts(_request(resources=many))["resources"] == MAX_RESOURCES + 15
