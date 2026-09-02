"""Mock provider: deterministic, evidence-shaped answers with no network.

Used in tests and whenever the operator selects it. Output is
built from the request itself so it mentions the findings, resources and
changes it was actually given, and streams in small chunks so the UI shows
streaming.
"""

import asyncio
from collections.abc import AsyncIterator

from app.assistant.base import LLMProvider
from app.assistant.prompt import (
    INVESTIGATION_HEADING,
    MODIFICATION_HEADING,
    NOT_EXECUTED_STATEMENT,
    SCRIPT_FENCE,
)
from app.models import AssistantRequest, AssistantStatus

CHUNK = 24
DELAY = 0.01


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, model: str = "mock") -> None:
        self.model = model
        self.last_stop_reason: str | None = None

    async def status(self) -> AssistantStatus:
        return AssistantStatus(available=True, provider=self.name, model=self.model)

    async def stream(self, request: AssistantRequest) -> AsyncIterator[str]:
        self.last_stop_reason = None
        text = render(request)
        for i in range(0, len(text), CHUNK):
            yield text[i : i + CHUNK]
            await asyncio.sleep(DELAY)
        self.last_stop_reason = "end_turn"

    async def ping(self) -> tuple[bool, str]:
        return True, "Mock provider selected. No live call made."


def render(request: AssistantRequest) -> str:
    if request.task == "generate-script":
        return _script(request)
    return _analysis(request)


def _analysis(request: AssistantRequest) -> str:
    ctx = request.context
    out: list[str] = []
    out.append("## Observed facts")
    if ctx.findings:
        for f in ctx.findings:
            where = f" on {f.resource_name}" if f.resource_name else ""
            out.append(f"- {f.severity.upper()}: {f.title}{where}. {f.summary}")
    else:
        out.append("- No findings were supplied.")
    if ctx.changes:
        out.append("")
        out.append("Recent changes in the evidence:")
        for c in ctx.changes:
            summary = c.summary or ", ".join(c.property_changes) or c.change_type
            out.append(f"- {c.resource_type} {c.resource_name}: {c.change_type}, {summary}")
    if ctx.resources:
        names = ", ".join(f"{r.name} ({r.type})" for r in ctx.resources[:10])
        more = f" and {len(ctx.resources) - 10} more" if len(ctx.resources) > 10 else ""
        out.append("")
        out.append(f"Resources in scope: {names}{more}.")

    out.append("")
    out.append("## Inferences")
    if ctx.findings and ctx.changes:
        f0 = ctx.findings[0]
        c0 = ctx.changes[0]
        out.append(
            f"- The change on {c0.resource_name} ({c0.summary or c0.change_type}) is the "
            f"most likely trigger for '{f0.title}'. This is an inference from timing and "
            "relationship, not an observed fact."
        )
    elif ctx.findings:
        out.append(
            "- No changes were supplied alongside the findings, so no cause can be inferred "
            "from the evidence. The findings stand as observed state only."
        )
    else:
        out.append("- Nothing to infer without findings.")

    out.append("")
    out.append("## Suggested investigation")
    if ctx.findings:
        for f in ctx.findings[:5]:
            target = f.resource_name or "the affected resource"
            out.append(
                f"- Confirm the current state of {target} directly in vCenter ({f.check_id})."
            )
        out.append("- Compare the previous and current snapshots for the same resources.")
    else:
        out.append("- Run a scan and select a finding so evidence can be attached.")

    out.append("")
    out.append("## Suggested remediation")
    recs = [f.recommendation for f in ctx.findings if f.recommendation]
    if recs:
        for r in recs[:5]:
            out.append(f"- {r} (review before acting; VCF Doctor makes no changes)")
    else:
        out.append("- No remediation is suggested until the investigation confirms a cause.")

    out.append("")
    out.append(f"Question asked: {ctx.question}")
    out.append("(Mock assistant: this answer was built from the supplied evidence only.)")
    return "\n".join(out)


def _script(request: AssistantRequest) -> str:
    ctx = request.context
    fmt = request.script_format or "powercli"
    fence = SCRIPT_FENCE.get(fmt, "")
    targets = [f.resource_name for f in ctx.findings if f.resource_name]
    targets += [r.name for r in ctx.resources if r.type == "host"]
    target = targets[0] if targets else "<host-name>"
    titles = "; ".join(f.title for f in ctx.findings) or "no findings supplied"

    inv, mod = _script_bodies(fmt, target)
    return "\n".join(
        [
            f"Script for: {titles}",
            f"Format: {fmt}. Question: {ctx.question}",
            "",
            INVESTIGATION_HEADING,
            "These commands only read state.",
            "",
            f"```{fence}",
            inv,
            "```",
            "",
            MODIFICATION_HEADING,
            "WARNING: the commands below change the environment. Review every line first.",
            "",
            f"```{fence}",
            mod,
            "```",
            "",
            NOT_EXECUTED_STATEMENT,
        ]
    )


def _script_bodies(fmt: str, target: str) -> tuple[str, str]:
    if fmt == "python":
        inv = "\n".join(
            [
                "# READ ONLY: connect and report the host connection state",
                "from pyVim.connect import SmartConnect",
                "from pyVmomi import vim",
                "si = SmartConnect(host='<vcenter-fqdn>', user='<user>', pwd='<credential>')",
                "content = si.RetrieveContent()",
                "view = content.viewManager.CreateContainerView("
                "content.rootFolder, [vim.HostSystem], True)",
                f"host = next(h for h in view.view if h.name == '{target}')",
                "# Print the observed state; make no changes",
                "print(host.name, host.runtime.connectionState)",
            ]
        )
        mod = "\n".join(
            [
                "# MODIFIES ENVIRONMENT: reconnect the host",
                "# Only run after the investigation confirms the host is reachable",
                "task = host.ReconnectHost_Task()",
            ]
        )
    elif fmt == "shell":
        inv = "\n".join(
            [
                "# READ ONLY: govc reads the host state; no changes are made",
                "export GOVC_URL='<vcenter-fqdn>' GOVC_USERNAME='<user>'",
                "export GOVC_PASSWORD='<credential>'",
                f"# Show connection state and recent events for {target}",
                f"govc host.info -host {target}",
                f"govc events -n 20 host/{target}",
            ]
        )
        mod = "\n".join(
            [
                "# MODIFIES ENVIRONMENT: reconnect the host",
                "# Only run after the investigation confirms the host is reachable",
                f"govc host.reconnect -host {target}",
            ]
        )
    elif fmt == "rest":
        inv = "\n".join(
            [
                "# READ ONLY: obtain a session token, then list the host and its state",
                "TOKEN=$(curl -sk -X POST -u '<user>:<credential>' "
                "https://<vcenter-fqdn>/api/session | tr -d '\"')",
                f"# GET is read only; filters to {target}",
                'curl -sk -H "vmware-api-session-id: $TOKEN" '
                f"'https://<vcenter-fqdn>/api/vcenter/host?names={target}'",
            ]
        )
        mod = "\n".join(
            [
                "# MODIFIES ENVIRONMENT: reconnect the host (POST changes state)",
                "# Only run after the investigation confirms the host is reachable",
                'curl -sk -X POST -H "vmware-api-session-id: $TOKEN" '
                "'https://<vcenter-fqdn>/api/vcenter/host/<host-id>?action=reconnect'",
            ]
        )
    else:
        inv = "\n".join(
            [
                "# READ ONLY: connect and report the host connection state",
                "Connect-VIServer -Server <vcenter-fqdn> -Credential (Get-Credential)",
                f"# Show the observed state of {target}; makes no changes",
                f"Get-VMHost -Name {target} | Select-Object Name, ConnectionState, PowerState",
                "# Recent events for the host, newest first",
                f"Get-VIEvent -Entity (Get-VMHost -Name {target}) -MaxSamples 20",
            ]
        )
        mod = "\n".join(
            [
                "# MODIFIES ENVIRONMENT: reconnect the host",
                "# Only run after the investigation confirms the host is reachable",
                f"Set-VMHost -VMHost {target} -State Connected -Confirm:$true",
            ]
        )
    return inv, mod
