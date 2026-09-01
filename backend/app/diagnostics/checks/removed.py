"""Checks that compare against the previous snapshot for removed resources."""

from app.diagnostics.base import DiagnosticCheck
from app.diagnostics.checks._common import by_id, finding_id
from app.models import Finding, Resource

# Types whose disappearance is reported by a more specific check or a
# more specific diff rule. RESOURCE_REMOVED covers everything else.
NETWORK_TYPES = {"network"}


def _removed(resources: list[Resource], previous: list[Resource]) -> list[Resource]:
    current_ids = set(by_id(resources))
    return [r for r in previous if r.id not in current_ids]


class NetworkRemoved(DiagnosticCheck):
    id = "NETWORK_REMOVED"
    name = "Network removed"
    description = "A network present in the previous snapshot is gone."

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        if previous is None:
            return []
        out: list[Finding] = []
        # VMs that referenced the network in the previous snapshot.
        for net in sorted(_removed(resources, previous), key=lambda r: r.id):
            if net.type not in NETWORK_TYPES:
                continue
            affected = sorted(
                vm.name
                for vm in previous
                if vm.type == "vm"
                and (
                    net.id in (vm.properties.get("networks") or [])
                    or net.name in (vm.properties.get("networks") or [])
                    or any(rel.target_id == net.id for rel in vm.relationships)
                )
            )
            out.append(
                Finding(
                    id=finding_id(self.id, net.id),
                    check_id=self.id,
                    severity="critical",
                    title=f"Network {net.name} was removed",
                    summary=f"Network {net.name} existed in the previous snapshot and is no "
                    f"longer present. {len(affected)} VM(s) referenced it.",
                    resource_id=net.id,
                    resource_name=net.name,
                    resource_type=net.type,
                    evidence={"previousProperties": net.properties, "affectedVms": affected},
                    recommendation=f"Confirm removal of {net.name} was intentional. If not, "
                    "recreate the port group with the same name and VLAN and reconnect "
                    "affected VM NICs.",
                )
            )
        return out


class ResourceRemoved(DiagnosticCheck):
    id = "RESOURCE_REMOVED"
    name = "Resource removed"
    description = "A resource present in the previous snapshot is gone."

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        if previous is None:
            return []
        out: list[Finding] = []
        for r in sorted(_removed(resources, previous), key=lambda r: r.id):
            if r.type in NETWORK_TYPES:
                continue
            if r.type == "vm" and r.name.startswith("vCLS"):
                # vSphere recreates vCLS VMs with new morefs; not an operator concern.
                continue
            out.append(
                Finding(
                    id=finding_id(self.id, r.id),
                    check_id=self.id,
                    severity="warning",
                    title=f"{r.type.capitalize()} {r.name} was removed",
                    summary=f"{r.type.capitalize()} {r.name} existed in the previous snapshot "
                    "and is no longer present.",
                    resource_id=r.id,
                    resource_name=r.name,
                    resource_type=r.type,
                    evidence={"previousProperties": r.properties, "parentId": r.parent_id},
                    recommendation=f"Confirm the removal of {r.type} {r.name} was intentional. "
                    "If it was not, check the collector connection and the object's "
                    "inventory state in vCenter.",
                )
            )
        return out
