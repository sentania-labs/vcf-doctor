"""Normalizer tests: fake RawObjects in, Resources out. No vCenter needed."""

from app.collectors.vsphere.normalize import (
    NETWORK_TYPES,
    PROPERTY_SPECS,
    RawInventory,
    RawObject,
    normalize,
    vcenter_key,
)
from app.models import Resource


def _inventory() -> RawInventory:
    objs = [
        RawObject("datacenter-2", "Datacenter", {"name": "DC1", "parent": "group-d1"}),
        RawObject("group-d1", "Folder", {"name": "Datacenters", "parent": None}),
        RawObject("group-h4", "Folder", {"name": "host", "parent": "datacenter-2"}),
        RawObject(
            "domain-c7",
            "ClusterComputeResource",
            {
                "name": "wld01",
                "parent": "group-h4",
                "host": ["host-12", "host-13"],
                "configuration.drsConfig.enabled": True,
                "configuration.dasConfig.enabled": False,
            },
        ),
        RawObject(
            "domain-s9",
            "ComputeResource",
            {"name": "esx-standalone.lab.local", "parent": "group-h4", "host": ["host-20"]},
        ),
        RawObject(
            "host-12",
            "HostSystem",
            {
                "name": "esx01.lab.local",
                "parent": "domain-c7",
                "runtime.connectionState": "connected",
                "runtime.powerState": "poweredOn",
                "runtime.inMaintenanceMode": False,
                "summary.hardware.cpuMhz": 2400,
                "summary.hardware.numCpuCores": 32,
                "summary.hardware.memorySize": 274877906944,
                "summary.config.product.version": "8.0.3",
                "summary.config.product.build": "24022510",
                "datastore": ["datastore-15", "datastore-16"],
                "overallStatus": "green",
            },
        ),
        RawObject(
            "host-13",
            "HostSystem",
            {
                "name": "esx02.lab.local",
                "parent": "domain-c7",
                "runtime.connectionState": "notResponding",
                "runtime.powerState": "unknown",
                "runtime.inMaintenanceMode": True,
                "datastore": [],
                "overallStatus": "red",
            },
        ),
        RawObject(
            "host-20",
            "HostSystem",
            {
                "name": "esx-standalone.lab.local",
                "parent": "domain-s9",
                "runtime.connectionState": "connected",
                "runtime.powerState": "poweredOn",
                "runtime.inMaintenanceMode": False,
                "summary.hardware.memorySize": 1024,
                "datastore": ["datastore-15"],
            },
        ),
        RawObject(
            "vm-101",
            "VirtualMachine",
            {
                "name": "app01",
                "runtime.powerState": "poweredOn",
                "runtime.host": "host-12",
                "summary.config.guestFullName": "Ubuntu Linux (64-bit)",
                "summary.config.numCpu": 4,
                "summary.config.memorySizeMB": 8192,
                "summary.config.template": False,
                "guest.toolsStatus": "toolsOk",
                "network": ["dvportgroup-20", "network-30"],
                "datastore": ["datastore-15"],
                "overallStatus": "green",
            },
        ),
        RawObject(
            "vm-102",
            "VirtualMachine",
            {
                "name": "orphan",
                "runtime.powerState": "poweredOff",
                "runtime.host": None,
                "summary.config.template": True,
                "network": [],
                "datastore": [],
            },
        ),
        RawObject(
            "datastore-15",
            "Datastore",
            {
                "name": "vsanDatastore",
                "summary.capacity": 10995116277760,
                "summary.freeSpace": 5497558138880,
                "summary.accessible": True,
                "summary.type": "vsan",
                "summary.url": "ds:///vmfs/volumes/vsan:1/",
            },
        ),
        RawObject(
            "datastore-16",
            "Datastore",
            {
                "name": "nfs01",
                "summary.capacity": 1000,
                "summary.freeSpace": 100,
                "summary.accessible": False,
                "summary.type": "NFS",
            },
        ),
        RawObject("dvportgroup-20", "DistributedVirtualPortgroup", {"name": "pg-mgmt"}),
        RawObject("network-30", "Network", {"name": "VM Network"}),
        RawObject("opaquenetwork-40", "OpaqueNetwork", {"name": "nsx-seg"}),
    ]
    return RawInventory(
        host="vc01.lab.local",
        name="vc01.lab.local",
        version="8.0.3",
        build="24022515",
        instance_uuid="6d3f0a5e-0000-4000-8000-000000000001",
        objects=objs,
    )


def _by_id(resources: list[Resource]) -> dict[str, Resource]:
    return {r.id: r for r in resources}


def test_vcenter_key_variants():
    assert vcenter_key("vc01.lab.local") == "vc01"
    assert vcenter_key("VC01") == "vc01"
    assert vcenter_key("192.168.1.10") == "192-168-1-10"
    assert vcenter_key("vc01.lab.local:8443") == "vc01"
    assert vcenter_key("[::1]:443") == "1"
    assert vcenter_key("") == "vcenter"


def test_ids_are_namespaced_and_stable():
    a = normalize(_inventory())
    b = normalize(_inventory())
    assert [r.id for r in a] == [r.id for r in b]
    assert all(r.source == "vcenter:vc01" for r in a)
    ids = {r.id for r in a}
    assert "vcenter:vc01" in ids
    assert "datacenter:vc01:datacenter-2" in ids
    assert "cluster:vc01:domain-c7" in ids
    assert "host:vc01:host-12" in ids
    assert "vm:vc01:vm-101" in ids
    assert "datastore:vc01:datastore-15" in ids
    assert "network:vc01:dvportgroup-20" in ids
    # folders and standalone ComputeResource are plumbing, not resources
    assert not any(r.id.endswith("group-h4") or r.id.endswith("domain-s9") for r in a)


def test_every_resource_roundtrips_as_json():
    for r in normalize(_inventory()):
        assert Resource.model_validate_json(r.model_dump_json()) == r


def test_vcenter_resource():
    vc = _by_id(normalize(_inventory()))["vcenter:vc01"]
    assert vc.type == "vcenter"
    assert vc.parent_id is None
    assert vc.properties["version"] == "8.0.3"
    assert vc.properties["build"] == "24022515"
    assert vc.properties["instanceUuid"].startswith("6d3f0a5e")


def test_datacenter_and_cluster():
    res = _by_id(normalize(_inventory()))
    dc = res["datacenter:vc01:datacenter-2"]
    assert dc.parent_id == "vcenter:vc01"
    cl = res["cluster:vc01:domain-c7"]
    assert cl.parent_id == "datacenter:vc01:datacenter-2"
    assert cl.properties["hostCount"] == 2
    assert cl.properties["drsEnabled"] is True
    assert cl.properties["haEnabled"] is False
    assert cl.properties["hosts"] == ["host:vc01:host-12", "host:vc01:host-13"]


def test_host_properties_and_relationships():
    res = _by_id(normalize(_inventory()))
    h = res["host:vc01:host-12"]
    assert h.parent_id == "cluster:vc01:domain-c7"
    p = h.properties
    assert p["connectionState"] == "connected"
    assert p["powerState"] == "poweredOn"
    assert p["maintenanceMode"] is False
    assert p["inMaintenance"] is False
    assert p["cpuMhz"] == 2400 and isinstance(p["cpuMhz"], int)
    assert p["numCpuCores"] == 32
    assert p["memoryBytes"] == 274877906944 and isinstance(p["memoryBytes"], int)
    assert p["version"] == "8.0.3"
    assert p["build"] == "24022510"
    assert p["cluster"] == "wld01"
    assert p["datastores"] == ["nfs01", "vsanDatastore"]
    kinds = {(r.kind, r.target_id) for r in h.relationships}
    assert ("member_of", "cluster:vc01:domain-c7") in kinds
    assert ("uses_datastore", "datastore:vc01:datastore-15") in kinds
    assert ("uses_datastore", "datastore:vc01:datastore-16") in kinds


def test_disconnected_host_missing_props_do_not_crash():
    h = _by_id(normalize(_inventory()))["host:vc01:host-13"]
    assert h.properties["connectionState"] == "notResponding"
    assert h.properties["maintenanceMode"] is True
    assert h.properties["cpuMhz"] is None
    assert h.properties["memoryBytes"] is None
    assert h.properties["version"] is None


def test_standalone_host_parents_to_datacenter():
    h = _by_id(normalize(_inventory()))["host:vc01:host-20"]
    assert h.parent_id == "datacenter:vc01:datacenter-2"
    assert h.properties["cluster"] is None
    assert not any(r.kind == "member_of" for r in h.relationships)


def test_vm_properties_and_relationships():
    vm = _by_id(normalize(_inventory()))["vm:vc01:vm-101"]
    assert vm.parent_id == "host:vc01:host-12"
    p = vm.properties
    assert p["powerState"] == "poweredOn"
    assert p["host"] == "esx01.lab.local"
    assert p["cluster"] == "wld01"
    assert p["guestFullName"] == "Ubuntu Linux (64-bit)"
    assert p["numCpu"] == 4
    assert p["memoryMB"] == 8192
    assert p["toolsStatus"] == "toolsOk"
    assert p["template"] is False
    assert p["networks"] == ["VM Network", "pg-mgmt"]
    assert p["datastores"] == ["vsanDatastore"]
    assert p["overallStatus"] == "green"
    rels = {(r.kind, r.target_id) for r in vm.relationships}
    assert rels == {
        ("runs_on", "host:vc01:host-12"),
        ("uses_network", "network:vc01:dvportgroup-20"),
        ("uses_network", "network:vc01:network-30"),
        ("uses_datastore", "datastore:vc01:datastore-15"),
    }


def test_orphan_template_vm():
    vm = _by_id(normalize(_inventory()))["vm:vc01:vm-102"]
    assert vm.parent_id == "vcenter:vc01"
    assert vm.properties["template"] is True
    assert vm.properties["host"] is None
    assert vm.relationships == []


def test_datastore_properties():
    res = _by_id(normalize(_inventory()))
    ds = res["datastore:vc01:datastore-15"]
    p = ds.properties
    assert p["capacity"] == 10995116277760 and isinstance(p["capacity"], int)
    assert p["freeSpace"] == 5497558138880
    assert p["capacityBytes"] == p["capacity"]
    assert p["freeBytes"] == p["freeSpace"]
    assert p["accessible"] is True
    assert p["type"] == "vsan"
    assert p["url"].startswith("ds://")
    assert res["datastore:vc01:datastore-16"].properties["accessible"] is False


def test_network_types():
    res = _by_id(normalize(_inventory()))
    assert res["network:vc01:dvportgroup-20"].properties["type"] == "dvportgroup"
    assert res["network:vc01:network-30"].properties["type"] == "standard"
    assert res["network:vc01:opaquenetwork-40"].properties["type"] == "opaque"
    assert all(res[k].properties["exists"] is True for k in res if k.startswith("network:"))


def test_property_specs_cover_network_subclasses():
    # Every network kind we classify must be reachable through the base view.
    assert "Network" in PROPERTY_SPECS
    assert set(NETWORK_TYPES) >= {"Network", "DistributedVirtualPortgroup", "OpaqueNetwork"}
