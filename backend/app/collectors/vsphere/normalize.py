"""Turn plain vSphere inventory records into VCF Doctor Resources.

This module never imports pyVmomi. The client layer flattens every managed
object into a `RawObject` (moref string, wsdl type name, dict of dotted
property paths to plain Python values). Everything here is pure Python so it
can be unit-tested with fake records.

ID scheme (stable across scans, namespaced per vCenter):

    source        vcenter:<vckey>
    vcenter       vcenter:<vckey>
    datacenter    datacenter:<vckey>:<moref>     e.g. datacenter:vc01:datacenter-2
    cluster       cluster:<vckey>:<moref>        e.g. cluster:vc01:domain-c7
    host          host:<vckey>:<moref>           e.g. host:vc01:host-12
    vm            vm:<vckey>:<moref>             e.g. vm:vc01:vm-101
    datastore     datastore:<vckey>:<moref>      e.g. datastore:vc01:datastore-15
    network       network:<vckey>:<moref>        e.g. network:vc01:dvportgroup-20

<vckey> is the first DNS label of the connection host, lowercased, with
anything outside [a-z0-9] replaced by "-" (so "vc01.lab.local" -> "vc01" and
"192.168.1.10" -> "192-168-1-10"). Managed object references (morefs) are
stable for the lifetime of an object inside one vCenter, survive renames and
vMotions, and are unique per vCenter, which is exactly what the diff engine
needs. A rebuilt object gets a new moref and therefore shows as removed +
added, which is the truthful answer.

Nested values. The PropertyCollector resolves dotted paths against the
DECLARED type of each property, not the runtime subclass, so a handful of
things we need (cluster EVC mode, VMFS version, dvportgroup VLAN, opaque
network type) can only be fetched as a whole parent object. The client
flattens those, and every list of DataObjects (vmkernel adapters, pnics,
virtual devices, host mounts, snapshot trees), into the small plain shapes
below. The normalizer only ever sees these shapes:

    ClusterComputeResource.summary      {"currentEVCModeKey", "totalCpu",
                                         "totalMemory", "numHosts"}
    ClusterComputeResource.configuration.rule
                                        [{"name", "enabled"}]
    HostSystem.config.network.vnic      [{"device", "ip", "mtu", "portgroup",
                                          "portgroupKey"}]
    HostSystem.config.network.pnic      [{"device", "mac", "linkSpeedMb"}]
    HostSystem.config.network.vswitch   [{"name"}]
    VirtualMachine.snapshot.rootSnapshotList
                                        [{"name", "createTime", "children": [...]}]
    VirtualMachine.config.hardware.device
                                        [{"kind": "disk", "label", "capacityBytes",
                                          "datastore", "thin"},
                                         {"kind": "nic", "label", "mac", "network",
                                          "portgroupKey", "opaqueNetworkId",
                                          "connected"}]
    Datastore.host                      [{"host", "mounted", "accessible"}]
    Datastore.info                      {"vmfsVersion"}
    DistributedVirtualPortgroup.config.defaultPortConfig
                                        {"vlan": {"kind": "id", "vlanId": N}
                                              | {"kind": "trunk", "ranges": [[a, b]]}
                                              | {"kind": "pvlan", "pvlanId": N}
                                              | None}
    OpaqueNetwork.summary               {"opaqueNetworkType", "opaqueNetworkId"}

Datetimes arrive as ISO 8601 strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.models import Relationship, Resource

# Property paths requested per managed object type. The client fetches
# exactly these with one PropertyCollector call per type. Every path must
# resolve against the declared type (tests/test_vsphere_property_paths.py).
PROPERTY_SPECS: dict[str, list[str]] = {
    "Datacenter": ["name", "parent"],
    "Folder": ["name", "parent"],
    "ResourcePool": ["name", "parent"],
    "DistributedVirtualSwitch": ["name"],
    "ComputeResource": ["name", "parent", "host"],
    "ClusterComputeResource": [
        "name",
        "parent",
        "host",
        "configuration.drsConfig.enabled",
        "configuration.drsConfig.defaultVmBehavior",
        "configuration.dasConfig.enabled",
        "configuration.dasConfig.admissionControlEnabled",
        "configuration.rule",
        # currentEVCModeKey lives on ClusterComputeResourceSummary, a subclass
        # of the declared ComputeResourceSummary, so the whole summary is
        # fetched and flattened by the client.
        "summary",
        "overallStatus",
    ],
    "HostSystem": [
        "name",
        "parent",
        "runtime.connectionState",
        "runtime.powerState",
        "runtime.inMaintenanceMode",
        "runtime.bootTime",
        "summary.hardware.cpuMhz",
        "summary.hardware.numCpuCores",
        "summary.hardware.memorySize",
        "summary.config.product.version",
        "summary.config.product.build",
        "summary.quickStats.uptime",
        "hardware.systemInfo.model",
        "hardware.systemInfo.vendor",
        "hardware.biosInfo.biosVersion",
        "config.lockdownMode",
        "config.dateTimeInfo.ntpConfig.server",
        "config.network.dnsConfig.address",
        "config.network.vnic",
        "config.network.pnic",
        "config.network.vswitch",
        "config.vsanHostConfig.enabled",
        "vm",
        "datastore",
        "overallStatus",
    ],
    "VirtualMachine": [
        "name",
        "parent",
        "resourcePool",
        "runtime.powerState",
        "runtime.connectionState",
        "runtime.host",
        "runtime.bootTime",
        "summary.config.guestFullName",
        "summary.config.numCpu",
        "summary.config.memorySizeMB",
        "summary.config.template",
        "summary.storage.committed",
        "guest.hostName",
        "guest.ipAddress",
        "guest.guestState",
        "guest.toolsStatus",
        "guest.toolsVersion",
        "config.version",
        "config.annotation",
        "config.cpuAllocation.reservation",
        "config.memoryAllocation.reservation",
        "config.hardware.device",
        "snapshot.rootSnapshotList",
        "network",
        "datastore",
        "overallStatus",
    ],
    "Datastore": [
        "name",
        "summary.capacity",
        "summary.freeSpace",
        "summary.accessible",
        "summary.type",
        "summary.url",
        "summary.multipleHostAccess",
        "summary.maintenanceMode",
        "host",
        # info.vmfs.version only exists on VmfsDatastoreInfo; fetched whole.
        "info",
        "overallStatus",
    ],
    "Network": ["name", "host", "overallStatus"],
    "DistributedVirtualPortgroup": [
        "key",
        "config.numPorts",
        "config.distributedVirtualSwitch",
        # defaultPortConfig.vlan only exists on VMwareDVSPortSetting; fetched whole.
        "config.defaultPortConfig",
    ],
    "OpaqueNetwork": ["summary"],
}

# ClusterComputeResource extends ComputeResource and the network subclasses
# extend Network, so a container view for the base type returns the
# subclasses too. The wsdl name tells them apart.
NETWORK_TYPES: dict[str, str] = {
    "Network": "standard",
    "DistributedVirtualPortgroup": "dvportgroup",
    "OpaqueNetwork": "opaque",
}


@dataclass
class RawObject:
    """One managed object flattened to plain values.

    moref: the vSphere managed object id, e.g. "vm-101".
    kind: the wsdl type name, e.g. "VirtualMachine".
    props: dotted property path -> plain value. Managed object references are
    rendered as moref strings; lists of references as lists of strings.
    """

    moref: str
    kind: str
    props: dict[str, Any] = field(default_factory=dict)

    def get(self, path: str, default: Any = None) -> Any:
        return self.props.get(path, default)


@dataclass
class RawInventory:
    """Everything the client pulled from one vCenter in one pass."""

    host: str
    name: str
    version: str | None
    build: str | None
    instance_uuid: str | None
    objects: list[RawObject] = field(default_factory=list)
    api_version: str | None = None
    os_type: str | None = None


_KEY_RE = re.compile(r"[^a-z0-9]+")


def vcenter_key(host: str) -> str:
    """First DNS label of the connection host, lowercased and sanitized."""
    label = host.strip().lower()
    if label.startswith("["):
        label = label[1:].split("]", 1)[0]
    elif label.count(":") == 1:
        label = label.split(":", 1)[0]
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", label):
        label = label.split(".", 1)[0]
    key = _KEY_RE.sub("-", label).strip("-")
    return key or "vcenter"


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _as_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _as_iso(value: Any) -> str | None:
    """Datetimes (from fake records) or ISO strings (from the client) -> ISO string."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    return [str(v) for v in value if v is not None]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in (value or []) if isinstance(v, dict)]


def _sort_key(item: dict[str, Any], *keys: str) -> tuple:
    return tuple(str(item.get(k) or "") for k in keys)


def format_vlan(spec: Any) -> int | str | None:
    """Render a flattened dvportgroup VLAN spec per the contract.

    {"kind": "id", "vlanId": 10}                      -> 10
    {"kind": "trunk", "ranges": [[1, 5], [7, 7]]}     -> "trunk 1-5,7"
    {"kind": "pvlan", "pvlanId": 200}                 -> "pvlan 200"
    """
    spec = _dict(spec)
    kind = spec.get("kind")
    if kind == "id":
        return _as_int(spec.get("vlanId"))
    if kind == "trunk":
        parts: list[str] = []
        ranges = sorted(
            (_as_int(r[0]), _as_int(r[1]))
            for r in (spec.get("ranges") or [])
            if isinstance(r, list | tuple) and len(r) == 2
        )
        for start, end in ranges:
            if start is None:
                continue
            parts.append(str(start) if end in (None, start) else f"{start}-{end}")
        return "trunk " + ",".join(parts) if parts else "trunk"
    if kind == "pvlan":
        pv = _as_int(spec.get("pvlanId"))
        return None if pv is None else f"pvlan {pv}"
    return None


def walk_snapshots(tree: Any) -> tuple[int, str | None]:
    """Count every snapshot in a flattened snapshot tree and find the oldest
    createTime (ISO string). Children live under "children"."""
    count = 0
    oldest: str | None = None
    stack = list(_dicts(tree))
    while stack:
        node = stack.pop()
        count += 1
        created = _as_iso(node.get("createTime"))
        if created is not None and (oldest is None or created < oldest):
            oldest = created
        stack.extend(_dicts(node.get("children")))
    return count, oldest


class Normalizer:
    """Build Resource objects from a RawInventory."""

    def __init__(self, inventory: RawInventory, key: str | None = None):
        """key overrides the id namespace (the connection id in production, so
        two vCenters sharing a first DNS label never collide)."""
        self.inv = inventory
        self.vckey = key or vcenter_key(inventory.host)
        self.source = f"vcenter:{self.vckey}"
        self.vcenter_id = self.source
        self.by_moref: dict[str, RawObject] = {o.moref: o for o in inventory.objects}
        self._dc_cache: dict[str, str | None] = {}
        # Lookup tables for names that are not reachable by moref alone.
        self.dvpg_by_key: dict[str, str] = {}
        self.opaque_by_id: dict[str, str] = {}
        self.vm_count_by_host: dict[str, int] = {}
        self.vm_count_by_cluster: dict[str, int] = {}
        self.vsan_hosts_by_cluster: dict[str, bool] = {}
        for o in inventory.objects:
            if o.kind == "DistributedVirtualPortgroup":
                self.dvpg_by_key[str(o.get("key") or o.moref)] = o.moref
            elif o.kind == "OpaqueNetwork":
                oid = _dict(o.get("summary")).get("opaqueNetworkId")
                if oid:
                    self.opaque_by_id[str(oid)] = o.moref
            elif o.kind == "HostSystem":
                cl = self.cluster_of_host(o)
                if cl:
                    vsan = bool(o.get("config.vsanHostConfig.enabled") or False)
                    self.vsan_hosts_by_cluster[cl] = (
                        self.vsan_hosts_by_cluster.get(cl, False) or vsan
                    )
        for o in inventory.objects:
            if o.kind != "VirtualMachine":
                continue
            host_moref = o.get("runtime.host")
            if not host_moref:
                continue
            self.vm_count_by_host[host_moref] = self.vm_count_by_host.get(host_moref, 0) + 1
            host_obj = self.by_moref.get(host_moref)
            cl = self.cluster_of_host(host_obj) if host_obj else None
            if cl:
                self.vm_count_by_cluster[cl] = self.vm_count_by_cluster.get(cl, 0) + 1

    # ---- id helpers -------------------------------------------------------

    def rid(self, rtype: str, moref: str) -> str:
        return f"{rtype}:{self.vckey}:{moref}"

    def name_of(self, moref: str | None) -> str | None:
        if moref is None:
            return None
        obj = self.by_moref.get(moref)
        return None if obj is None else _as_str(obj.get("name"))

    def names_of(self, morefs: Any) -> list[str]:
        return sorted(filter(None, (self.name_of(m) for m in (morefs or []))))

    def datacenter_of(self, moref: str | None) -> str | None:
        """Walk parent links until a Datacenter is found. Returns its moref."""
        seen: set[str] = set()
        cur = moref
        while cur is not None and cur not in seen:
            if cur in self._dc_cache:
                return self._dc_cache[cur]
            seen.add(cur)
            obj = self.by_moref.get(cur)
            if obj is None:
                break
            if obj.kind == "Datacenter":
                for m in seen:
                    self._dc_cache[m] = cur
                return cur
            cur = obj.get("parent")
        for m in seen:
            self._dc_cache[m] = None
        return None

    def cluster_of_host(self, host: RawObject) -> str | None:
        parent = host.get("parent")
        if parent is None:
            return None
        pobj = self.by_moref.get(parent)
        if pobj is not None and pobj.kind == "ClusterComputeResource":
            return parent
        return None

    def network_name(
        self,
        moref: str | None = None,
        portgroup_key: str | None = None,
        opaque_id: str | None = None,
    ) -> str | None:
        """Resolve whichever handle a backing or vnic carries to a network name."""
        if moref:
            name = self.name_of(moref)
            if name:
                return name
        if portgroup_key:
            name = self.name_of(self.dvpg_by_key.get(str(portgroup_key)))
            if name:
                return name
        if opaque_id:
            return self.name_of(self.opaque_by_id.get(str(opaque_id)))
        return None

    # ---- builders -----------------------------------------------------------

    def build(self) -> list[Resource]:
        out: list[Resource] = [self.vcenter_resource()]
        builders = {
            "Datacenter": self.datacenter,
            "ClusterComputeResource": self.cluster,
            "HostSystem": self.host,
            "VirtualMachine": self.vm,
            "Datastore": self.datastore,
        }
        for obj in self.inv.objects:
            fn = builders.get(obj.kind)
            if fn is not None:
                out.append(fn(obj))
            elif obj.kind in NETWORK_TYPES:
                out.append(self.network(obj))
        return out

    def vcenter_resource(self) -> Resource:
        return Resource(
            id=self.vcenter_id,
            type="vcenter",
            name=self.inv.name,
            source=self.source,
            parent_id=None,
            properties={
                "name": self.inv.name,
                "host": self.inv.host,
                "version": self.inv.version,
                "build": self.inv.build,
                "apiVersion": self.inv.api_version,
                "instanceUuid": self.inv.instance_uuid,
                "osType": self.inv.os_type,
            },
        )

    def datacenter(self, obj: RawObject) -> Resource:
        return Resource(
            id=self.rid("datacenter", obj.moref),
            type="datacenter",
            name=str(obj.get("name")),
            source=self.source,
            parent_id=self.vcenter_id,
            properties={"moref": obj.moref},
        )

    def cluster(self, obj: RawObject) -> Resource:
        hosts = list(obj.get("host") or [])
        dc = self.datacenter_of(obj.get("parent"))
        summary = _dict(obj.get("summary"))
        return Resource(
            id=self.rid("cluster", obj.moref),
            type="cluster",
            name=str(obj.get("name")),
            source=self.source,
            parent_id=self.rid("datacenter", dc) if dc else self.vcenter_id,
            properties={
                "moref": obj.moref,
                "hostCount": len(hosts),
                "hosts": sorted(self.rid("host", h) for h in hosts),
                "drsEnabled": bool(obj.get("configuration.drsConfig.enabled") or False),
                "drsAutomationLevel": _as_str(obj.get("configuration.drsConfig.defaultVmBehavior")),
                "haEnabled": bool(obj.get("configuration.dasConfig.enabled") or False),
                "haAdmissionControl": bool(
                    obj.get("configuration.dasConfig.admissionControlEnabled") or False
                ),
                "evcMode": _as_str(summary.get("currentEVCModeKey")) or None,
                # vsanConfigInfo sits on ClusterConfigInfoEx, which the
                # PropertyCollector cannot reach through configurationEx, so
                # vSAN is derived from the member hosts' vsanHostConfig.
                "vsanEnabled": self.vsan_hosts_by_cluster.get(obj.moref, False),
                "ruleCount": len(obj.get("configuration.rule") or []),
                "totalCpuMhz": _as_int(summary.get("totalCpu")),
                "totalMemoryBytes": _as_int(summary.get("totalMemory")),
                "numVms": self.vm_count_by_cluster.get(obj.moref, 0),
                "datacenter": self.name_of(dc),
                "overallStatus": _as_str(obj.get("overallStatus")),
            },
        )

    def host(self, obj: RawObject) -> Resource:
        cluster = self.cluster_of_host(obj)
        dc = self.datacenter_of(obj.get("parent"))
        if cluster:
            parent_id = self.rid("cluster", cluster)
        elif dc:
            parent_id = self.rid("datacenter", dc)
        else:
            parent_id = self.vcenter_id
        maint = bool(obj.get("runtime.inMaintenanceMode") or False)
        datastores = list(obj.get("datastore") or [])
        rels = []
        if cluster:
            rels.append(Relationship(kind="member_of", target_id=self.rid("cluster", cluster)))
        rels.extend(
            Relationship(kind="uses_datastore", target_id=self.rid("datastore", d))
            for d in datastores
        )
        # A disconnected host has no config at all. Only report empty NTP/DNS
        # lists when the config was actually readable, so the NTP check does
        # not fire on hosts vCenter cannot see.
        config_seen = any(k.startswith("config.") for k in obj.props)
        ntp = _as_str_list(obj.get("config.dateTimeInfo.ntpConfig.server"))
        dns = _as_str_list(obj.get("config.network.dnsConfig.address"))
        vmks = [
            {
                "device": _as_str(v.get("device")),
                "ip": _as_str(v.get("ip")),
                "mtu": _as_int(v.get("mtu")),
                "portgroup": _as_str(v.get("portgroup"))
                or self.network_name(portgroup_key=v.get("portgroupKey")),
            }
            for v in _dicts(obj.get("config.network.vnic"))
        ]
        pnics = [
            {
                "device": _as_str(p.get("device")),
                "mac": _as_str(p.get("mac")),
                "linkSpeedMb": _as_int(p.get("linkSpeedMb")),
            }
            for p in _dicts(obj.get("config.network.pnic"))
        ]
        vswitches = sorted(
            filter(
                None, (_as_str(s.get("name")) for s in _dicts(obj.get("config.network.vswitch")))
            )
        )
        if "vm" in obj.props:
            num_vms = len(obj.get("vm") or [])
        else:
            num_vms = self.vm_count_by_host.get(obj.moref, 0)
        return Resource(
            id=self.rid("host", obj.moref),
            type="host",
            name=str(obj.get("name")),
            source=self.source,
            parent_id=parent_id,
            properties={
                "moref": obj.moref,
                "connectionState": _as_str(obj.get("runtime.connectionState")),
                "powerState": _as_str(obj.get("runtime.powerState")),
                "maintenanceMode": maint,
                "inMaintenance": maint,
                "cluster": self.name_of(cluster),
                "datacenter": self.name_of(dc),
                "version": _as_str(obj.get("summary.config.product.version")),
                "build": _as_str(obj.get("summary.config.product.build")),
                "model": _as_str(obj.get("hardware.systemInfo.model")),
                "vendor": _as_str(obj.get("hardware.systemInfo.vendor")),
                "biosVersion": _as_str(obj.get("hardware.biosInfo.biosVersion")),
                "cpuMhz": _as_int(obj.get("summary.hardware.cpuMhz")),
                "numCpuCores": _as_int(obj.get("summary.hardware.numCpuCores")),
                "memoryBytes": _as_int(obj.get("summary.hardware.memorySize")),
                "uptimeSeconds": _as_int(obj.get("summary.quickStats.uptime")),
                "bootTime": _as_iso(obj.get("runtime.bootTime")),
                "lockdownMode": _as_str(obj.get("config.lockdownMode")),
                "ntpServers": ntp if (ntp or config_seen) else None,
                "dnsServers": dns if (dns or config_seen) else None,
                "vmkernelAdapters": sorted(vmks, key=lambda v: _sort_key(v, "device")),
                "physicalNics": sorted(pnics, key=lambda p: _sort_key(p, "device")),
                "standardSwitches": vswitches,
                "vsanEnabled": _as_bool(obj.get("config.vsanHostConfig.enabled")),
                "numVms": num_vms,
                "datastores": self.names_of(datastores),
                "overallStatus": _as_str(obj.get("overallStatus")),
            },
            relationships=rels,
        )

    def vm(self, obj: RawObject) -> Resource:
        host_moref = obj.get("runtime.host")
        host_obj = self.by_moref.get(host_moref) if host_moref else None
        cluster = self.cluster_of_host(host_obj) if host_obj else None
        networks = list(obj.get("network") or [])
        datastores = list(obj.get("datastore") or [])
        rels = []
        if host_moref:
            rels.append(Relationship(kind="runs_on", target_id=self.rid("host", host_moref)))
        rels.extend(
            Relationship(kind="uses_network", target_id=self.rid("network", n)) for n in networks
        )
        rels.extend(
            Relationship(kind="uses_datastore", target_id=self.rid("datastore", d))
            for d in datastores
        )
        disks: list[dict[str, Any]] = []
        nics: list[dict[str, Any]] = []
        for dev in _dicts(obj.get("config.hardware.device")):
            if dev.get("kind") == "disk":
                disks.append(
                    {
                        "label": _as_str(dev.get("label")),
                        "capacityBytes": _as_int(dev.get("capacityBytes")),
                        "datastore": self.name_of(dev.get("datastore")),
                        "thin": _as_bool(dev.get("thin")),
                    }
                )
            elif dev.get("kind") == "nic":
                nics.append(
                    {
                        "label": _as_str(dev.get("label")),
                        "mac": _as_str(dev.get("mac")),
                        "network": self.network_name(
                            dev.get("network"), dev.get("portgroupKey"), dev.get("opaqueNetworkId")
                        ),
                        "connected": _as_bool(dev.get("connected")),
                    }
                )
        snap_count, oldest = walk_snapshots(obj.get("snapshot.rootSnapshotList"))
        parent = obj.get("parent")
        parent_obj = self.by_moref.get(parent) if parent else None
        folder = (
            _as_str(parent_obj.get("name")) if parent_obj and parent_obj.kind == "Folder" else None
        )
        return Resource(
            id=self.rid("vm", obj.moref),
            type="vm",
            name=str(obj.get("name")),
            source=self.source,
            parent_id=self.rid("host", host_moref) if host_moref else self.vcenter_id,
            properties={
                "moref": obj.moref,
                "powerState": _as_str(obj.get("runtime.powerState")),
                "connectionState": _as_str(obj.get("runtime.connectionState")),
                "host": self.name_of(host_moref),
                "cluster": self.name_of(cluster),
                "resourcePool": self.name_of(obj.get("resourcePool")),
                "folder": folder,
                "guestFullName": _as_str(obj.get("summary.config.guestFullName")),
                "guestHostname": _as_str(obj.get("guest.hostName")),
                "guestIp": _as_str(obj.get("guest.ipAddress")),
                "guestState": _as_str(obj.get("guest.guestState")),
                "toolsStatus": _as_str(obj.get("guest.toolsStatus")),
                "toolsVersion": _as_str(obj.get("guest.toolsVersion")),
                "numCpu": _as_int(obj.get("summary.config.numCpu")),
                "memoryMB": _as_int(obj.get("summary.config.memorySizeMB")),
                "hardwareVersion": _as_str(obj.get("config.version")),
                "template": bool(obj.get("summary.config.template") or False),
                "cpuReservationMhz": _as_int(obj.get("config.cpuAllocation.reservation")),
                "memReservationMB": _as_int(obj.get("config.memoryAllocation.reservation")),
                "annotation": _as_str(obj.get("config.annotation")),
                "snapshotCount": snap_count,
                "oldestSnapshotTime": oldest,
                "disks": sorted(disks, key=lambda d: _sort_key(d, "label")),
                "nics": sorted(nics, key=lambda n: _sort_key(n, "label")),
                "networks": self.names_of(networks),
                "datastores": self.names_of(datastores),
                "storageCommittedBytes": _as_int(obj.get("summary.storage.committed")),
                "bootTime": _as_iso(obj.get("runtime.bootTime")),
                "overallStatus": _as_str(obj.get("overallStatus")),
            },
            relationships=rels,
        )

    def datastore(self, obj: RawObject) -> Resource:
        capacity = _as_int(obj.get("summary.capacity"))
        free = _as_int(obj.get("summary.freeSpace"))
        mounts = _dicts(obj.get("host"))
        return Resource(
            id=self.rid("datastore", obj.moref),
            type="datastore",
            name=str(obj.get("name")),
            source=self.source,
            parent_id=self.vcenter_id,
            properties={
                "moref": obj.moref,
                "capacity": capacity,
                "freeSpace": free,
                "capacityBytes": capacity,
                "freeBytes": free,
                "accessible": bool(obj.get("summary.accessible") or False),
                "type": _as_str(obj.get("summary.type")),
                "url": _as_str(obj.get("summary.url")),
                "hosts": self.names_of(m.get("host") for m in mounts),
                "multipleHostAccess": _as_bool(obj.get("summary.multipleHostAccess")),
                "maintenanceMode": _as_str(obj.get("summary.maintenanceMode")),
                "vmfsVersion": _as_str(_dict(obj.get("info")).get("vmfsVersion")),
                "overallStatus": _as_str(obj.get("overallStatus")),
            },
        )

    def network(self, obj: RawObject) -> Resource:
        ntype = NETWORK_TYPES.get(obj.kind, "standard")
        props: dict[str, Any] = {
            "moref": obj.moref,
            "type": ntype,
            "vlan": None,
            "numPorts": None,
            "switch": None,
            "hosts": None,
            "exists": True,
            "overallStatus": _as_str(obj.get("overallStatus")),
        }
        if ntype == "dvportgroup":
            props["vlan"] = format_vlan(_dict(obj.get("config.defaultPortConfig")).get("vlan"))
            props["numPorts"] = _as_int(obj.get("config.numPorts"))
            props["switch"] = self.name_of(obj.get("config.distributedVirtualSwitch"))
        elif ntype == "standard":
            props["hosts"] = self.names_of(obj.get("host"))
        else:
            props["opaqueNetworkType"] = _as_str(_dict(obj.get("summary")).get("opaqueNetworkType"))
        return Resource(
            id=self.rid("network", obj.moref),
            type="network",
            name=str(obj.get("name")),
            source=self.source,
            parent_id=self.vcenter_id,
            properties=props,
        )


def normalize(inventory: RawInventory, key: str | None = None) -> list[Resource]:
    """Convenience wrapper: RawInventory -> list[Resource]."""
    return Normalizer(inventory, key).build()
