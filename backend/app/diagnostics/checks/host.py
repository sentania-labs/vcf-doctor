"""Host health checks."""

from app.diagnostics.base import DiagnosticCheck
from app.diagnostics.checks._common import by_id, by_type, cluster_members, make_finding
from app.models import Finding, Resource


class HostDisconnected(DiagnosticCheck):
    id = "HOST_DISCONNECTED"
    name = "Host disconnected"
    description = "An ESXi host is disconnected from vCenter."
    resource_type = "host"

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
    resource_type = "host"

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
    resource_type = "host"

    def applicable(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Resource]:
        hosts = by_type(resources, "host")
        return [h for h in hosts if h.properties.get("maintenanceMode") is not None]

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


class HostNtpNotConfigured(DiagnosticCheck):
    id = "HOST_NTP_NOT_CONFIGURED"
    name = "Host NTP not configured"
    description = "An ESXi host has no NTP servers configured."
    resource_type = "host"

    def applicable(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Resource]:
        # Only hosts whose collector reported an NTP list were judged.
        return [
            h for h in by_type(resources, "host")
            if isinstance(h.properties.get("ntpServers"), list | tuple)
        ]

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        out: list[Finding] = []
        for h in by_type(resources, "host"):
            # Absent or null means the collector did not report it (older
            # snapshots, fixtures); only an explicit empty list is a finding.
            servers = h.properties.get("ntpServers")
            if not isinstance(servers, list | tuple) or len(servers) > 0:
                continue
            out.append(
                make_finding(
                    self.id,
                    h,
                    "warning",
                    f"Host {h.name} has no NTP servers configured",
                    f"Host {h.name} reports an empty NTP server list. Clock drift on an ESXi "
                    "host breaks vSAN, NSX, SSO token validation and log correlation.",
                    {"ntpServers": list(servers), "cluster": h.properties.get("cluster")},
                    f"Configure NTP on {h.name} (Configure, System, Time Configuration): add "
                    "the same NTP servers the rest of the cluster uses, set the service to "
                    "start with the host, and start it. Apply via host profile or the "
                    "cluster's time configuration where available.",
                )
            )
        return out


class HostVersionMismatch(DiagnosticCheck):
    id = "HOST_VERSION_MISMATCH"
    name = "Host version mismatch in cluster"
    description = "Hosts in one cluster run different ESXi versions or builds."
    resource_type = "cluster"

    def applicable(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Resource]:
        # A cluster is judged when at least one member host reports a version or build.
        hosts = by_id(by_type(resources, "host"))
        members = cluster_members(resources)
        out: list[Resource] = []
        for cluster in by_type(resources, "cluster"):
            for hid in members.get(cluster.id, set()):
                h = hosts.get(hid)
                if h is None:
                    continue
                if h.properties.get("version") is not None or h.properties.get("build") is not None:
                    out.append(cluster)
                    break
        return out

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        hosts = by_id(by_type(resources, "host"))
        members = cluster_members(resources)
        out: list[Finding] = []
        for cluster in sorted(by_type(resources, "cluster"), key=lambda c: c.id):
            per_host: dict[str, str] = {}
            for hid in sorted(members.get(cluster.id, set())):
                h = hosts.get(hid)
                if h is None:
                    continue
                version = h.properties.get("version")
                build = h.properties.get("build")
                if version is None and build is None:
                    continue  # not reported by this collector; cannot judge
                per_host[h.name] = f"{version or 'unknown'} build {build or 'unknown'}"
            distinct = sorted(set(per_host.values()))
            if len(distinct) < 2:
                continue
            out.append(
                make_finding(
                    self.id,
                    cluster,
                    "warning",
                    f"Cluster {cluster.name} hosts run {len(distinct)} different ESXi builds",
                    f"Cluster {cluster.name} has hosts on different ESXi versions or builds "
                    f"({'; '.join(distinct)}). Mixed builds complicate vMotion/EVC "
                    "compatibility, HA behaviour and support cases.",
                    {"hosts": per_host, "versions": distinct},
                    f"Bring every host in {cluster.name} to the same ESXi version and build: "
                    "finish the in-progress upgrade with Lifecycle Manager (remediate the "
                    "cluster image or baseline), or roll back the outliers if the upgrade "
                    "was not intended yet.",
                )
            )
        return out
