"""Host health checks."""

from app.diagnostics.base import DiagnosticCheck
from app.diagnostics.checks._common import by_type, make_finding
from app.models import Finding, Resource


class HostDisconnected(DiagnosticCheck):
    id = "HOST_DISCONNECTED"
    name = "Host disconnected"
    description = "An ESXi host is disconnected from vCenter."

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        out: list[Finding] = []
        for h in by_type(resources, "host"):
            state = h.properties.get("connectionState")
            if state == "disconnected":
                out.append(
                    make_finding(
                        self.id,
                        h,
                        "critical",
                        f"Host {h.name} is disconnected",
                        f"Host {h.name} reports connectionState=disconnected. vCenter cannot "
                        "manage or monitor workloads on this host.",
                        {"connectionState": state, "cluster": h.properties.get("cluster")},
                        f"Reconnect {h.name} in vCenter (right-click the host, Connection, "
                        "Connect). If the reconnect fails, verify the host is powered on, "
                        "management network is reachable, and the vpxa service is running.",
                    )
                )
        return out


class HostNotResponding(DiagnosticCheck):
    id = "HOST_NOT_RESPONDING"
    name = "Host not responding"
    description = "An ESXi host is not responding to vCenter heartbeats."

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        out: list[Finding] = []
        for h in by_type(resources, "host"):
            state = h.properties.get("connectionState")
            if state == "notResponding":
                out.append(
                    make_finding(
                        self.id,
                        h,
                        "critical",
                        f"Host {h.name} is not responding",
                        f"Host {h.name} reports connectionState=notResponding. vCenter has lost "
                        "heartbeats from the host; VMs on it may be isolated or down.",
                        {"connectionState": state, "cluster": h.properties.get("cluster")},
                        f"Check whether {h.name} is powered on and reachable on its management "
                        "network. Confirm HA has restarted its VMs elsewhere, then restart the "
                        "management agents (services.sh restart) or reboot the host.",
                    )
                )
        return out


class HostMaintenanceMode(DiagnosticCheck):
    id = "HOST_MAINTENANCE_MODE"
    name = "Host in maintenance mode"
    description = "An ESXi host is in maintenance mode and not running workloads."

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        out: list[Finding] = []
        for h in by_type(resources, "host"):
            if h.properties.get("maintenanceMode") is True:
                out.append(
                    make_finding(
                        self.id,
                        h,
                        "warning",
                        f"Host {h.name} is in maintenance mode",
                        f"Host {h.name} is in maintenance mode. Its capacity is unavailable to "
                        "the cluster and DRS will not place VMs on it.",
                        {"maintenanceMode": True, "cluster": h.properties.get("cluster")},
                        f"If maintenance on {h.name} is complete, exit maintenance mode to return "
                        "its capacity to the cluster. If maintenance is intentional, confirm the "
                        "remaining hosts satisfy HA admission control.",
                    )
                )
        return out
