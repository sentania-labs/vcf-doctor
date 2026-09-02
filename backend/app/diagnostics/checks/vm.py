"""Virtual machine checks."""

from datetime import UTC, datetime

from app.diagnostics.base import DiagnosticCheck
from app.diagnostics.checks._common import by_type, is_template, make_finding
from app.models import Finding, Resource

BAD_CONNECTION_STATES = {"orphaned", "inaccessible", "invalid"}


class VmPoweredOff(DiagnosticCheck):
    id = "VM_POWERED_OFF"
    name = "VM powered off"
    description = "A virtual machine (not a template) is powered off."
    resource_type = "vm"

    def applicable(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Resource]:
        return [vm for vm in by_type(resources, "vm") if not is_template(vm)]

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
    resource_type = "vm"

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


SNAPSHOT_MAX_AGE_DAYS = 7
SNAPSHOT_MAX_COUNT = 3
TOOLS_NOT_RUNNING_STATES = {"toolsNotRunning", "toolsNotInstalled"}


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


class VmSnapshotStale(DiagnosticCheck):
    id = "VM_SNAPSHOT_STALE"
    name = "VM snapshot stale"
    description = (
        f"A VM has a snapshot older than {SNAPSHOT_MAX_AGE_DAYS} days or more than "
        f"{SNAPSHOT_MAX_COUNT} snapshots."
    )
    resource_type = "vm"

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        now = datetime.now(UTC)
        out: list[Finding] = []
        for vm in by_type(resources, "vm"):
            p = vm.properties
            count = _as_int(p.get("snapshotCount"))
            oldest = _parse_iso(p.get("oldestSnapshotTime"))
            age_days = None
            if oldest is not None:
                age_days = max(0, int((now - oldest).total_seconds() // 86400))
            reasons: list[str] = []
            if age_days is not None and age_days > SNAPSHOT_MAX_AGE_DAYS:
                reasons.append(f"oldest snapshot is {age_days} days old")
            if count is not None and count > SNAPSHOT_MAX_COUNT:
                reasons.append(f"{count} snapshots")
            if not reasons:
                continue
            out.append(
                make_finding(
                    self.id,
                    vm,
                    "warning",
                    f"VM {vm.name} has stale snapshots",
                    f"VM {vm.name}: {', '.join(reasons)}. Snapshot chains grow with every "
                    "write, consume datastore capacity and slow the VM; they are not backups.",
                    {
                        "snapshotCount": count,
                        "oldestSnapshotTime": p.get("oldestSnapshotTime"),
                        "oldestSnapshotAgeDays": age_days,
                        "maxAgeDays": SNAPSHOT_MAX_AGE_DAYS,
                        "maxCount": SNAPSHOT_MAX_COUNT,
                        "host": p.get("host"),
                    },
                    f"Review the snapshots on {vm.name} in Snapshot Manager and delete (which "
                    "consolidates) the ones no longer needed. If a snapshot must be kept, "
                    "replace it with a backup or clone. Check the datastore has free space "
                    "for the consolidation first.",
                )
            )
        return out


class VmToolsNotRunning(DiagnosticCheck):
    id = "VM_TOOLS_NOT_RUNNING"
    name = "VMware Tools not running"
    description = "A powered-on VM (not a template) has VMware Tools not running or not installed."
    resource_type = "vm"

    def applicable(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Resource]:
        # Only powered-on, non-template VMs can be judged on Tools state.
        return [
            vm for vm in by_type(resources, "vm")
            if not is_template(vm) and vm.properties.get("powerState") == "poweredOn"
        ]

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        out: list[Finding] = []
        for vm in by_type(resources, "vm"):
            if is_template(vm):
                continue
            p = vm.properties
            status = p.get("toolsStatus")
            if p.get("powerState") != "poweredOn" or status not in TOOLS_NOT_RUNNING_STATES:
                continue
            out.append(
                make_finding(
                    self.id,
                    vm,
                    "info",
                    f"VMware Tools not running on {vm.name}",
                    f"VM {vm.name} is powered on but reports toolsStatus={status}. Without "
                    "Tools, vCenter has no guest IP or hostname, no heartbeat for HA VM "
                    "monitoring, and cannot shut the guest down gracefully.",
                    {
                        "toolsStatus": status,
                        "toolsVersion": p.get("toolsVersion"),
                        "powerState": p.get("powerState"),
                        "guestFullName": p.get("guestFullName"),
                        "host": p.get("host"),
                    },
                    (
                        f"Install VMware Tools (or open-vm-tools) in the guest on {vm.name}."
                        if status == "toolsNotInstalled"
                        else f"Start the VMware Tools service in the guest on {vm.name} "
                        "(vmtoolsd / open-vm-tools); if it will not start, reinstall Tools. "
                        "If the guest is still booting, re-check after the next scan."
                    ),
                )
            )
        return out
