"""Virtual machine checks."""

from app.diagnostics.base import DiagnosticCheck
from app.diagnostics.checks._common import by_type, is_template, make_finding
from app.models import Finding, Resource

BAD_CONNECTION_STATES = {"orphaned", "inaccessible", "invalid"}


class VmPoweredOff(DiagnosticCheck):
    id = "VM_POWERED_OFF"
    name = "VM powered off"
    description = "A virtual machine (not a template) is powered off."

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        out: list[Finding] = []
        for vm in by_type(resources, "vm"):
            if is_template(vm):
                continue
            if vm.properties.get("powerState") == "poweredOff":
                out.append(
                    make_finding(
                        self.id,
                        vm,
                        "info",
                        f"VM {vm.name} is powered off",
                        f"VM {vm.name} is powered off.",
                        {"powerState": "poweredOff", "host": vm.properties.get("host")},
                        f"If {vm.name} should be running, power it on. If it is retired, "
                        "consider deleting it to reclaim datastore space.",
                    )
                )
        return out


class VmOrphanedOrInaccessible(DiagnosticCheck):
    id = "VM_ORPHANED_OR_INACCESSIBLE"
    name = "VM orphaned or inaccessible"
    description = "A virtual machine is orphaned, inaccessible, or invalid."

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        out: list[Finding] = []
        for vm in by_type(resources, "vm"):
            p = vm.properties
            state = p.get("connectionState")
            reasons: list[str] = []
            if state in BAD_CONNECTION_STATES:
                reasons.append(f"connectionState={state}")
            if p.get("orphaned") is True:
                reasons.append("orphaned=true")
            if p.get("inaccessible") is True:
                reasons.append("inaccessible=true")
            if not reasons:
                continue
            out.append(
                make_finding(
                    self.id,
                    vm,
                    "critical",
                    f"VM {vm.name} is orphaned or inaccessible",
                    f"VM {vm.name} cannot be managed: {', '.join(reasons)}. Its files may be "
                    "on an inaccessible datastore or the host it was registered on is gone.",
                    {
                        "connectionState": state,
                        "overallStatus": p.get("overallStatus"),
                        "host": p.get("host"),
                        "datastores": p.get("datastores"),
                        "reasons": reasons,
                    },
                    f"Check that the datastores backing {vm.name} are accessible and its host "
                    "is connected. If the files exist, remove the VM from inventory and "
                    "re-register it from its .vmx file. If the files are gone, remove it from "
                    "inventory.",
                )
            )
        return out
