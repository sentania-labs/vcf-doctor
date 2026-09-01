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
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.models import Relationship, Resource

# Property paths requested per managed object type. The client fetches
# exactly these with one PropertyCollector call per type.
PROPERTY_SPECS: dict[str, list[str]] = {
    "Datacenter": ["name", "parent"],
    "Folder": ["name", "parent"],
    "ComputeResource": ["name", "parent", "host"],
    "ClusterComputeResource": [
        "name",
        "parent",
        "host",
        "configuration.drsConfig.enabled",
        "configuration.dasConfig.enabled",
    ],
    "HostSystem": [
        "name",
        "parent",
        "runtime.connectionState",
        "runtime.powerState",
        "runtime.inMaintenanceMode",
        "summary.hardware.cpuMhz",
        "summary.hardware.numCpuCores",
        "summary.hardware.memorySize",
        "summary.config.product.version",
        "summary.config.product.build",
        "datastore",
        "overallStatus",
    ],
    "VirtualMachine": [
        "name",
        "runtime.powerState",
        "runtime.connectionState",
        "runtime.host",
        "summary.config.guestFullName",
        "summary.config.numCpu",
        "summary.config.memorySizeMB",
        "summary.config.template",
        "guest.toolsStatus",
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
        "overallStatus",
    ],
    "Network": ["name", "overallStatus"],
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

    # ---- id helpers -------------------------------------------------------

    def rid(self, rtype: str, moref: str) -> str:
        return f"{rtype}:{self.vckey}:{moref}"

    def name_of(self, moref: str | None) -> str | None:
        if moref is None:
            return None
        obj = self.by_moref.get(moref)
        return None if obj is None else _as_str(obj.get("name"))

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
                "host": self.inv.host,
                "version": self.inv.version,
                "build": self.inv.build,
                "instanceUuid": self.inv.instance_uuid,
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
                "haEnabled": bool(obj.get("configuration.dasConfig.enabled") or False),
                "datacenter": self.name_of(dc),
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
                "cpuMhz": _as_int(obj.get("summary.hardware.cpuMhz")),
                "numCpuCores": _as_int(obj.get("summary.hardware.numCpuCores")),
                "memoryBytes": _as_int(obj.get("summary.hardware.memorySize")),
                "version": _as_str(obj.get("summary.config.product.version")),
                "build": _as_str(obj.get("summary.config.product.build")),
                "cluster": self.name_of(cluster),
                "datacenter": self.name_of(dc),
                "datastores": sorted(filter(None, (self.name_of(d) for d in datastores))),
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
        return Resource(
            id=self.rid("vm", obj.moref),
            type="vm",
            name=str(obj.get("name")),
            source=self.source,
            parent_id=self.rid("host", host_moref) if host_moref else self.vcenter_id,
            properties={
                "moref": obj.moref,
                "powerState": _as_str(obj.get("runtime.powerState")),
                "host": self.name_of(host_moref),
                "cluster": self.name_of(cluster),
                "guestFullName": _as_str(obj.get("summary.config.guestFullName")),
                "numCpu": _as_int(obj.get("summary.config.numCpu")),
                "memoryMB": _as_int(obj.get("summary.config.memorySizeMB")),
                "toolsStatus": _as_str(obj.get("guest.toolsStatus")),
                "connectionState": _as_str(obj.get("runtime.connectionState")),
                "template": bool(obj.get("summary.config.template") or False),
                "networks": sorted(filter(None, (self.name_of(n) for n in networks))),
                "datastores": sorted(filter(None, (self.name_of(d) for d in datastores))),
                "overallStatus": _as_str(obj.get("overallStatus")),
            },
            relationships=rels,
        )

    def datastore(self, obj: RawObject) -> Resource:
        capacity = _as_int(obj.get("summary.capacity"))
        free = _as_int(obj.get("summary.freeSpace"))
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
                "overallStatus": _as_str(obj.get("overallStatus")),
            },
        )

    def network(self, obj: RawObject) -> Resource:
        return Resource(
            id=self.rid("network", obj.moref),
            type="network",
            name=str(obj.get("name")),
            source=self.source,
            parent_id=self.vcenter_id,
            properties={
                "moref": obj.moref,
                "type": NETWORK_TYPES.get(obj.kind, "standard"),
                "exists": True,
                "overallStatus": _as_str(obj.get("overallStatus")),
            },
        )


def normalize(inventory: RawInventory, key: str | None = None) -> list[Resource]:
    """Convenience wrapper: RawInventory -> list[Resource]."""
    return Normalizer(inventory, key).build()
