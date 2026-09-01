"""Prompt construction for the VCF Doctor assistant.

The system prompt is startup.md section 18 verbatim, followed by task
guidance. The user message is the evidence package serialized as compact
JSON blocks. Evidence is capped so a huge inventory cannot blow the request
up; when capped, the model is told the evidence is partial.
"""

import json
from typing import Any

from app.models import AssistantRequest

# Evidence caps. Beyond these the block is truncated and a note is added.
MAX_FINDINGS = 40
MAX_CHANGES = 80
MAX_RESOURCES = 60

EVIDENCE_RULE = (
    "Only make factual statements about the environment that are supported "
    "by the supplied evidence."
)

# startup.md section 18, verbatim.
SAFETY_PROMPT = """You are the VCF Doctor assistant.

VCF Doctor has supplied deterministic observations from a VMware environment.

Treat supplied findings, resources, changes, and evidence as authoritative.

Do not invent VMware resources, states, versions, alarms, configuration values, log entries, API responses, or environmental facts that do not appear in the evidence.

Clearly distinguish:

1. Observed facts
2. Inferences
3. Suggested investigation
4. Suggested remediation

When generating scripts or commands:

- make them reviewable rather than automatically executable;
- prefer read-only investigation before modification;
- clearly identify commands that modify infrastructure;
- include comments explaining the purpose of each important command;
- do not assume credentials or endpoints that are not provided.

If evidence is insufficient, state what additional evidence should be collected."""  # noqa: E501

TASK_GUIDANCE = f"""
Additional guidance:

- {EVIDENCE_RULE}
- The evidence arrives as JSON blocks labelled FINDINGS, CHANGES and RESOURCES.
  A block may carry a "truncated" note; if so, treat the evidence as partial and
  say so where it matters.
- Refer to resources by the names given in the evidence. Do not guess hostnames,
  IP addresses, versions or credentials.
- Use Markdown headings for the four categories: "## Observed facts",
  "## Inferences", "## Suggested investigation", "## Suggested remediation".
  Omit a heading only when there is nothing to say under it, and say why.
- VCF Doctor never executes anything you produce. Every command is for operator review.
"""

SCRIPT_FORMAT_LABELS = {
    "powercli": "PowerCLI (PowerShell with VMware.PowerCLI)",
    "python": "Python (pyVmomi or the vSphere Automation SDK)",
    "shell": "shell (bash using govc and/or esxcli)",
    "rest": "REST (curl against the vSphere Automation API)",
}

SCRIPT_FENCE = {
    "powercli": "powershell",
    "python": "python",
    "shell": "bash",
    "rest": "bash",
}

INVESTIGATION_HEADING = "## Investigation (read only)"
MODIFICATION_HEADING = "## Modification (changes environment)"
NOT_EXECUTED_STATEMENT = (
    "Nothing in this response is executed by VCF Doctor. "
    "All commands are provided for operator review only."
)


def build_system_prompt(request: AssistantRequest) -> str:
    parts = [SAFETY_PROMPT, TASK_GUIDANCE]
    if request.task == "generate-script":
        fmt = request.script_format or "powercli"
        parts.append(
            "Script generation rules:\n"
            f"- Write the script in {SCRIPT_FORMAT_LABELS.get(fmt, fmt)}. "
            "Do not switch to another language or tool.\n"
            "- Structure the answer with exactly these two sections, titled "
            f'"{INVESTIGATION_HEADING}" and "{MODIFICATION_HEADING}", in that order.\n'
            f"- Put code inside fenced code blocks tagged {SCRIPT_FENCE.get(fmt, '')}.\n"
            "- Comment every command so an operator can review it line by line.\n"
            "- The investigation section must only read state. Anything that changes the "
            "environment belongs under the modification section, and each modifying command "
            "must carry a comment marking it as a change.\n"
            "- Use placeholders such as <vcenter-fqdn> and <credential> instead of assuming "
            "endpoints or credentials.\n"
            f'- End with the sentence: "{NOT_EXECUTED_STATEMENT}"'
        )
    return "\n".join(parts)


def _task_instruction(request: AssistantRequest) -> str:
    task = request.task
    if task == "explain":
        return (
            "Task: explain. Explain what the supplied findings mean, in operational terms, "
            "for an infrastructure administrator. Separate observed facts from inference."
        )
    if task == "investigate":
        return (
            "Task: investigate. Propose a read-only investigation plan for the supplied "
            "findings: what to check, where, and what each result would tell us. Prefer "
            "steps that narrow the cause before any change is suggested."
        )
    if task == "generate-script":
        fmt = request.script_format or "powercli"
        return (
            f"Task: generate-script in {SCRIPT_FORMAT_LABELS.get(fmt, fmt)}. "
            "Produce a reviewable script for the supplied findings with the two required "
            "sections (investigation first, modification second)."
        )
    return "Task: answer the operator's question using only the supplied evidence."


def _dump(items: list[Any], cap: int, label: str) -> str:
    total = len(items)
    shown = items[:cap]
    payload: dict[str, Any] = {
        "count": total,
        "items": [i.model_dump(mode="json", exclude_none=True) for i in shown],
    }
    if total > cap:
        payload["truncated"] = (
            f"Only the first {cap} of {total} {label} are included. "
            "Evidence is partial; do not assume the omitted items are healthy or unchanged."
        )
    return json.dumps(payload, separators=(",", ":"), default=str)


def build_user_message(request: AssistantRequest) -> str:
    ctx = request.context
    lines = [
        _task_instruction(request),
        "",
        f"Question: {ctx.question}",
        f"Allowed actions: {', '.join(ctx.allowed_actions) or 'read-only'}",
        "",
        EVIDENCE_RULE,
        "",
        "FINDINGS:",
        _dump(ctx.findings, MAX_FINDINGS, "findings"),
        "",
        "CHANGES:",
        _dump(ctx.changes, MAX_CHANGES, "changes"),
        "",
        "RESOURCES:",
        _dump(ctx.resources, MAX_RESOURCES, "resources"),
    ]
    if not (ctx.findings or ctx.changes or ctx.resources):
        lines += [
            "",
            "No findings, changes or resources were supplied. Say what evidence "
            "should be collected instead of speculating.",
        ]
    return "\n".join(lines)


def evidence_counts(request: AssistantRequest) -> dict[str, int]:
    ctx = request.context
    return {
        "findings": len(ctx.findings),
        "changes": len(ctx.changes),
        "resources": len(ctx.resources),
    }
