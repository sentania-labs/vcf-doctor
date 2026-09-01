"""Cluster membership checks."""

from app.diagnostics.base import DiagnosticCheck
from app.diagnostics.checks._common import by_id, by_type, cluster_members, make_finding
from app.models import Finding, Resource

MIN_HOSTS = 2


class ClusterHostCountChange(DiagnosticCheck):
    id = "CLUSTER_HOST_COUNT_CHANGE"
    name = "Cluster host count changed"
    description = "The number of hosts in a cluster differs from the previous snapshot."

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        if previous is None:
            return []
        now = cluster_members(resources)
        before = cluster_members(previous)
        clusters = by_id(by_type(resources, "cluster"))
        out: list[Finding] = []
        for cid, cluster in sorted(clusters.items()):
            if cid not in before:
                continue
            old_n = len(before[cid])
            new_n = len(now.get(cid, set()))
            if old_n == new_n:
                continue
            added = sorted(now.get(cid, set()) - before[cid])
            removed = sorted(before[cid] - now.get(cid, set()))
            direction = "dropped" if new_n < old_n else "grew"
            out.append(
                make_finding(
                    self.id,
                    cluster,
                    "warning",
                    f"Cluster {cluster.name} host count {direction} from {old_n} to {new_n}",
                    f"Cluster {cluster.name} had {old_n} hosts in the previous snapshot and "
                    f"now has {new_n}.",
                    {
                        "previousHostCount": old_n,
                        "currentHostCount": new_n,
                        "hostsAdded": added,
                        "hostsRemoved": removed,
                    },
                    f"Confirm the host membership change in {cluster.name} was intentional. "
                    "If a host was removed unexpectedly, check for disconnected or "
                    "not-responding hosts and verify HA admission control still holds.",
                )
            )
        return out


class HostCountLow(DiagnosticCheck):
    id = "HOST_COUNT_LOW"
    name = "Cluster host count low"
    description = f"A cluster has fewer than {MIN_HOSTS} hosts and cannot tolerate a failure."

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        members = cluster_members(resources)
        out: list[Finding] = []
        for cluster in by_type(resources, "cluster"):
            n = len(members.get(cluster.id, set()))
            if n >= MIN_HOSTS:
                continue
            out.append(
                make_finding(
                    self.id,
                    cluster,
                    "warning",
                    f"Cluster {cluster.name} has only {n} host{'' if n == 1 else 's'}",
                    f"Cluster {cluster.name} has {n} host(s). HA cannot fail workloads over "
                    "and DRS has nowhere to balance to.",
                    {
                        "hostCount": n,
                        "minimum": MIN_HOSTS,
                        "hosts": sorted(members.get(cluster.id, set())),
                    },
                    f"Add at least one more host to {cluster.name}, or move its workloads to a "
                    "cluster with redundancy.",
                )
            )
        return out


class ClusterHaDisabled(DiagnosticCheck):
    id = "CLUSTER_HA_DISABLED"
    name = "Cluster HA disabled"
    description = "vSphere HA is disabled on a cluster."

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        out: list[Finding] = []
        for cluster in by_type(resources, "cluster"):
            if cluster.properties.get("haEnabled") is not False:
                continue  # True, or not reported (older snapshots): nothing to say
            n_vms = cluster.properties.get("numVms")
            vm_note = f" Its {n_vms} VMs" if isinstance(n_vms, int) else " Its VMs"
            out.append(
                make_finding(
                    self.id,
                    cluster,
                    "warning",
                    f"vSphere HA is disabled on {cluster.name}",
                    f"Cluster {cluster.name} has vSphere HA disabled.{vm_note} will not be "
                    "restarted automatically after a host failure.",
                    {
                        "haEnabled": False,
                        "haAdmissionControl": cluster.properties.get("haAdmissionControl"),
                        "hostCount": cluster.properties.get("hostCount"),
                        "numVms": n_vms,
                    },
                    f"Enable vSphere HA on {cluster.name} (Configure, Services, vSphere "
                    "Availability) with admission control sized for at least one host "
                    "failure. If HA was turned off for maintenance, re-enable it once the "
                    "work is done.",
                )
            )
        return out


class ClusterDrsDisabled(DiagnosticCheck):
    id = "CLUSTER_DRS_DISABLED"
    name = "Cluster DRS disabled"
    description = "vSphere DRS is disabled on a cluster."

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        out: list[Finding] = []
        for cluster in by_type(resources, "cluster"):
            if cluster.properties.get("drsEnabled") is not False:
                continue
            out.append(
                make_finding(
                    self.id,
                    cluster,
                    "info",
                    f"DRS is disabled on {cluster.name}",
                    f"Cluster {cluster.name} has DRS disabled. Load is not balanced across "
                    "hosts, maintenance mode will not evacuate VMs automatically, and vCLS "
                    "placement is unmanaged.",
                    {
                        "drsEnabled": False,
                        "drsAutomationLevel": cluster.properties.get("drsAutomationLevel"),
                        "hostCount": cluster.properties.get("hostCount"),
                    },
                    f"Enable DRS on {cluster.name} (Configure, Services, vSphere DRS). Start "
                    "with partially automated if you want to review migrations before moving "
                    "to fully automated.",
                )
            )
        return out
