"""Normalizer tests: fake RawObjects in, Resources out. No vCenter needed.

The fake records use the flattened shapes documented in normalize.py (the
same ones client.to_plain emits), so this covers the normalizer end to end
without pyVmomi.
"""

from app.collectors.vsphere.normalize import (
    NETWORK_TYPES,
    PROPERTY_SPECS,
    RawInventory,
    RawObject,
    format_vlan,
    normalize,
    vcenter_key,
    walk_snapshots,
)
from app.models import Resource


def _inventory() -> RawInventory:
    objs = [
        RawObject("datacenter-2", "Datacenter", {"name": "DC1", "parent": "group-d1"}),
        RawObject("group-d1", "Folder", {"name": "Datacenters", "parent": None}),
        RawObject("group-h4", "Folder", {"name": "host", "parent": "datacenter-2"}),
        RawObject("group-v3", "Folder", {"name": "vm", "parent": "datacenter-2"}),
        RawObject("group-v50", "Folder", {"name": "Prod", "parent": "group-v3"}),
        RawObject("resgroup-8", "ResourcePool", {"name": "Resources", "parent": "domain-c7"}),
        RawObject("resgroup-60", "ResourcePool", {"name": "gold", "parent": "resgroup-8"}),
        RawObject("dvs-10", "VmwareDistributedVirtualSwitch", {"name": "dvs-wld01"}),
        RawObject(
            "domain-c7",
            "ClusterComputeResource",
            {
                "name": "wld01",
                "parent": "group-h4",
                "host": ["host-12", "host-13"],
                "configuration.drsConfig.enabled": True,
                "configuration.drsConfig.defaultVmBehavior": "fullyAutomated",
                "configuration.dasConfig.enabled": False,
                "configuration.dasConfig.admissionControlEnabled": True,
                "configuration.rule": [
                    {"name": "keep-apart", "enabled": True},
                    {"name": "keep-together", "enabled": False},
                ],
                "summary": {
                    "currentEVCModeKey": "intel-icelake",
                    "totalCpu": 153600,
                    "totalMemory": 549755813888,
                    "numHosts": 2,
                },
                "overallStatus": "yellow",
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
                "runtime.bootTime": "2026-07-01T08:00:00+00:00",
                "summary.hardware.cpuMhz": 2400,
                "summary.hardware.numCpuCores": 32,
                "summary.hardware.memorySize": 274877906944,
                "summary.config.product.version": "8.0.3",
                "summary.config.product.build": "24022510",
                "summary.quickStats.uptime": 5356800,
                "hardware.systemInfo.model": "PowerEdge R760",
                "hardware.systemInfo.vendor": "Dell Inc.",
                "hardware.biosInfo.biosVersion": "2.3.4",
                "config.lockdownMode": "lockdownNormal",
                "config.dateTimeInfo.ntpConfig.server": ["10.0.0.2", "10.0.0.1"],
                "config.network.dnsConfig.address": ["10.0.0.53"],
                "config.network.vnic": [
                    {
                        "device": "vmk1",
                        "ip": "10.0.1.11",
                        "mtu": 9000,
                        "portgroup": None,
                        "portgroupKey": "dvportgroup-21",
                    },
                    {
                        "device": "vmk0",
                        "ip": "10.0.0.11",
                        "mtu": 1500,
                        "portgroup": "Management Network",
                        "portgroupKey": None,
                    },
                ],
                "config.network.pnic": [
                    {"device": "vmnic1", "mac": "aa:bb:cc:00:00:02", "linkSpeedMb": None},
                    {"device": "vmnic0", "mac": "aa:bb:cc:00:00:01", "linkSpeedMb": 25000},
                ],
                "config.network.vswitch": [{"name": "vSwitch1"}, {"name": "vSwitch0"}],
                "config.vsanHostConfig.enabled": True,
                "vm": ["vm-101", "vm-103"],
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
                "config.lockdownMode": "lockdownDisabled",
                "config.network.vswitch": [{"name": "vSwitch0"}],
                "datastore": ["datastore-15"],
            },
        ),
        RawObject(
            "vm-101",
            "VirtualMachine",
            {
                "name": "app01",
                "parent": "group-v50",
                "resourcePool": "resgroup-60",
                "runtime.powerState": "poweredOn",
                "runtime.connectionState": "connected",
                "runtime.host": "host-12",
                "runtime.bootTime": "2026-08-15T09:30:00+00:00",
                "summary.config.guestFullName": "Ubuntu Linux (64-bit)",
                "summary.config.numCpu": 4,
                "summary.config.memorySizeMB": 8192,
                "summary.config.template": False,
                "summary.storage.committed": 53687091200,
                "guest.hostName": "app01.lab.local",
                "guest.ipAddress": "10.0.2.31",
                "guest.guestState": "running",
                "guest.toolsStatus": "toolsOk",
                "guest.toolsVersion": "12384",
                "config.version": "vmx-21",
                "config.annotation": "billing app",
                "config.cpuAllocation.reservation": 1000,
                "config.memoryAllocation.reservation": 2048,
                "config.hardware.device": [
                    {
                        "kind": "disk",
                        "label": "Hard disk 2",
                        "capacityBytes": 214748364800,
                        "datastore": "datastore-16",
                        "thin": False,
                    },
                    {
                        "kind": "nic",
                        "label": "Network adapter 2",
                        "mac": "00:50:56:aa:bb:02",
                        "network": "network-30",
                        "portgroupKey": None,
                        "opaqueNetworkId": None,
                        "connected": False,
                    },
                    {
                        "kind": "disk",
                        "label": "Hard disk 1",
                        "capacityBytes": 107374182400,
                        "datastore": "datastore-15",
                        "thin": True,
                    },
                    {
                        "kind": "nic",
                        "label": "Network adapter 1",
                        "mac": "00:50:56:aa:bb:01",
                        "network": None,
                        "portgroupKey": "dvportgroup-20",
                        "opaqueNetworkId": None,
                        "connected": True,
                    },
                    {
                        "kind": "nic",
                        "label": "Network adapter 3",
                        "mac": "00:50:56:aa:bb:03",
                        "network": None,
                        "portgroupKey": None,
                        "opaqueNetworkId": "seg-1",
                        "connected": True,
                    },
                ],
                "snapshot.rootSnapshotList": [
                    {
                        "name": "before-patch",
                        "createTime": "2026-08-01T12:00:00+00:00",
                        "children": [
                            {
                                "name": "after-patch",
                                "createTime": "2026-08-20T12:00:00+00:00",
                                "children": [],
                            }
                        ],
                    }
                ],
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
            "vm-103",
            "VirtualMachine",
            {
                "name": "app02",
                "parent": "group-v3",
                "resourcePool": "resgroup-8",
                "runtime.powerState": "poweredOn",
                "runtime.host": "host-12",
                "network": [],
                "datastore": ["datastore-15"],
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
                "summary.multipleHostAccess": True,
                "summary.maintenanceMode": "normal",
                "host": [
                    {"host": "host-13", "mounted": True, "accessible": False},
                    {"host": "host-12", "mounted": True, "accessible": True},
                ],
                "info": {"vmfsVersion": None},
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
        RawObject(
            "datastore-17",
            "Datastore",
            {
                "name": "local-esx01",
                "summary.capacity": 500,
                "summary.freeSpace": 250,
                "summary.accessible": True,
                "summary.type": "VMFS",
                "summary.multipleHostAccess": False,
                "host": [{"host": "host-12", "mounted": True, "accessible": True}],
                "info": {"vmfsVersion": "6.82"},
            },
        ),
        RawObject(
            "dvportgroup-20",
            "DistributedVirtualPortgroup",
            {
                "name": "pg-mgmt",
                "key": "dvportgroup-20",
                "config.numPorts": 8,
                "config.distributedVirtualSwitch": "dvs-10",
                "config.defaultPortConfig": {"vlan": {"kind": "id", "vlanId": 10}},
            },
        ),
        RawObject(
            "dvportgroup-21",
            "DistributedVirtualPortgroup",
            {
                "name": "pg-trunk",
                "key": "dvportgroup-21",
                "config.numPorts": 128,
                "config.distributedVirtualSwitch": "dvs-10",
                "config.defaultPortConfig": {
                    "vlan": {"kind": "trunk", "ranges": [[100, 110], [5, 5], [200, 205]]}
                },
            },
        ),
        RawObject(
            "dvportgroup-22",
            "DistributedVirtualPortgroup",
            {
                "name": "pg-pvlan",
                "key": "dvportgroup-22",
                "config.numPorts": 8,
                "config.distributedVirtualSwitch": "dvs-10",
                "config.defaultPortConfig": {"vlan": {"kind": "pvlan", "pvlanId": 301}},
            },
        ),
        RawObject("network-30", "Network", {"name": "VM Network", "host": ["host-20", "host-12"]}),
        RawObject(
            "opaquenetwork-40",
            "OpaqueNetwork",
            {
                "name": "nsx-seg",
                "host": ["host-12"],
                "summary": {"opaqueNetworkType": "nsx.LogicalSwitch", "opaqueNetworkId": "seg-1"},
            },
        ),
    ]
    return RawInventory(
        host="vc01.lab.local",
        name="vc01.lab.local",
        version="8.0.3",
        build="24022515",
        instance_uuid="6d3f0a5e-0000-4000-8000-000000000001",
        objects=objs,
        api_version="8.0.3.0",
        os_type="linux-x64",
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
    # folders, pools, switches and standalone ComputeResource are plumbing, not resources
    for plumbing in ("group-h4", "domain-s9", "resgroup-8", "dvs-10", "group-v50"):
        assert not any(r.id.endswith(plumbing) for r in a), plumbing


def test_every_resource_roundtrips_as_json():
    for r in normalize(_inventory()):
        assert Resource.model_validate_json(r.model_dump_json()) == r


def test_vcenter_resource():
    vc = _by_id(normalize(_inventory()))["vcenter:vc01"]
    assert vc.type == "vcenter"
    assert vc.parent_id is None
    p = vc.properties
    assert p["name"] == "vc01.lab.local"
    assert p["version"] == "8.0.3"
    assert p["build"] == "24022515"
    assert p["apiVersion"] == "8.0.3.0"
    assert p["instanceUuid"].startswith("6d3f0a5e")
    assert p["osType"] == "linux-x64"


def test_datacenter_and_cluster():
    res = _by_id(normalize(_inventory()))
    dc = res["datacenter:vc01:datacenter-2"]
    assert dc.parent_id == "vcenter:vc01"
    cl = res["cluster:vc01:domain-c7"]
    assert cl.parent_id == "datacenter:vc01:datacenter-2"
    p = cl.properties
    assert p["hostCount"] == 2
    assert p["hosts"] == ["host:vc01:host-12", "host:vc01:host-13"]
    assert p["drsEnabled"] is True
    assert p["drsAutomationLevel"] == "fullyAutomated"
    assert p["haEnabled"] is False
    assert p["haAdmissionControl"] is True
    assert p["evcMode"] == "intel-icelake"
    assert p["vsanEnabled"] is True  # derived from esx01's vsanHostConfig
    assert p["ruleCount"] == 2
    assert p["totalCpuMhz"] == 153600
    assert p["totalMemoryBytes"] == 549755813888
    assert p["numVms"] == 2  # app01 and app02 on esx01; the orphan has no host
    assert p["overallStatus"] == "yellow"


def test_cluster_without_summary_or_vsan_hosts():
    inv = _inventory()
    for o in inv.objects:
        if o.kind == "ClusterComputeResource":
            o.props.pop("summary")
            o.props.pop("configuration.rule")
        if o.kind == "HostSystem":
            o.props.pop("config.vsanHostConfig.enabled", None)
    p = _by_id(normalize(inv))["cluster:vc01:domain-c7"].properties
    assert p["evcMode"] is None
    assert p["vsanEnabled"] is None  # no host reported a value: unknown, not False
    assert p["ruleCount"] == 0
    assert p["totalCpuMhz"] is None and p["totalMemoryBytes"] is None


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
    assert p["datacenter"] == "DC1"
    assert p["datastores"] == ["nfs01", "vsanDatastore"]
    kinds = {(r.kind, r.target_id) for r in h.relationships}
    assert ("member_of", "cluster:vc01:domain-c7") in kinds
    assert ("uses_datastore", "datastore:vc01:datastore-15") in kinds
    assert ("uses_datastore", "datastore:vc01:datastore-16") in kinds


def test_host_deep_properties():
    p = _by_id(normalize(_inventory()))["host:vc01:host-12"].properties
    assert p["model"] == "PowerEdge R760"
    assert p["vendor"] == "Dell Inc."
    assert p["biosVersion"] == "2.3.4"
    assert p["uptimeSeconds"] == 5356800
    assert p["bootTime"] == "2026-07-01T08:00:00+00:00"
    assert p["lockdownMode"] == "lockdownNormal"
    assert p["ntpServers"] == ["10.0.0.2", "10.0.0.1"]  # order as configured
    assert p["dnsServers"] == ["10.0.0.53"]
    # sorted by device; the DVS-backed vmk resolves its portgroup by key
    assert p["vmkernelAdapters"] == [
        {"device": "vmk0", "ip": "10.0.0.11", "mtu": 1500, "portgroup": "Management Network"},
        {"device": "vmk1", "ip": "10.0.1.11", "mtu": 9000, "portgroup": "pg-trunk"},
    ]
    assert p["physicalNics"] == [
        {"device": "vmnic0", "mac": "aa:bb:cc:00:00:01", "linkSpeedMb": 25000},
        {"device": "vmnic1", "mac": "aa:bb:cc:00:00:02", "linkSpeedMb": None},
    ]
    assert p["standardSwitches"] == ["vSwitch0", "vSwitch1"]
    assert p["numVms"] == 2
    assert p["vsanEnabled"] is True


def test_disconnected_host_missing_props_do_not_crash():
    h = _by_id(normalize(_inventory()))["host:vc01:host-13"]
    p = h.properties
    assert p["connectionState"] == "notResponding"
    assert p["maintenanceMode"] is True
    assert p["cpuMhz"] is None
    assert p["memoryBytes"] is None
    assert p["version"] is None
    assert p["model"] is None and p["lockdownMode"] is None and p["bootTime"] is None
    # config unreadable: unknown, not "no NTP configured"
    assert p["ntpServers"] is None
    assert p["dnsServers"] is None
    # config.network.* was not returned: unknown, not "no adapters".
    assert p["vmkernelAdapters"] is None
    assert p["physicalNics"] is None
    assert p["standardSwitches"] is None
    assert p["numVms"] == 0
    assert p["vsanEnabled"] is None


def test_connected_host_with_no_ntp_reports_empty_list():
    # config was readable (a vswitch came back) but ntp/dns are unset
    p = _by_id(normalize(_inventory()))["host:vc01:host-20"].properties
    assert p["ntpServers"] == []
    assert p["dnsServers"] == []
    assert p["lockdownMode"] == "lockdownDisabled"


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


def test_vm_deep_properties():
    p = _by_id(normalize(_inventory()))["vm:vc01:vm-101"].properties
    assert p["connectionState"] == "connected"
    assert p["resourcePool"] == "gold"
    assert p["folder"] == "Prod"
    assert p["guestHostname"] == "app01.lab.local"
    assert p["guestIp"] == "10.0.2.31"
    assert p["guestState"] == "running"
    assert p["toolsVersion"] == "12384"
    assert p["hardwareVersion"] == "vmx-21"
    assert p["cpuReservationMhz"] == 1000
    assert p["memReservationMB"] == 2048
    assert p["annotation"] == "billing app"
    assert p["storageCommittedBytes"] == 53687091200
    assert p["bootTime"] == "2026-08-15T09:30:00+00:00"


def test_vm_snapshots_are_counted_recursively_with_oldest_time():
    p = _by_id(normalize(_inventory()))["vm:vc01:vm-101"].properties
    assert p["snapshotCount"] == 2
    assert p["oldestSnapshotTime"] == "2026-08-01T12:00:00+00:00"
    # vm-102 returned no config (orphan): snapshot state is unknown, not zero.
    none = _by_id(normalize(_inventory()))["vm:vc01:vm-102"].properties
    assert none["snapshotCount"] is None
    assert none["oldestSnapshotTime"] is None


def test_walk_snapshots_handles_deep_trees_and_garbage():
    tree = [
        {
            "name": "a",
            "createTime": "2026-05-01T00:00:00+00:00",
            "children": [
                {
                    "name": "b",
                    "createTime": "2026-04-01T00:00:00+00:00",
                    "children": [
                        {"name": "c", "createTime": "2026-06-01T00:00:00+00:00"},
                    ],
                },
            ],
        },
        {"name": "d", "createTime": None},
    ]
    assert walk_snapshots(tree) == (4, "2026-04-01T00:00:00+00:00")
    assert walk_snapshots(None) == (0, None)
    assert walk_snapshots("not a list") == (0, None)


def test_vm_disks_and_nics_are_resolved_and_sorted():
    p = _by_id(normalize(_inventory()))["vm:vc01:vm-101"].properties
    assert p["disks"] == [
        {
            "label": "Hard disk 1",
            "capacityBytes": 107374182400,
            "datastore": "vsanDatastore",
            "thin": True,
        },
        {
            "label": "Hard disk 2",
            "capacityBytes": 214748364800,
            "datastore": "nfs01",
            "thin": False,
        },
    ]
    # dvportgroup key, plain network moref, and opaque network id all resolve to names
    assert p["nics"] == [
        {
            "label": "Network adapter 1",
            "mac": "00:50:56:aa:bb:01",
            "network": "pg-mgmt",
            "connected": True,
        },
        {
            "label": "Network adapter 2",
            "mac": "00:50:56:aa:bb:02",
            "network": "VM Network",
            "connected": False,
        },
        {
            "label": "Network adapter 3",
            "mac": "00:50:56:aa:bb:03",
            "network": "nsx-seg",
            "connected": True,
        },
    ]


def test_orphan_template_vm():
    vm = _by_id(normalize(_inventory()))["vm:vc01:vm-102"]
    assert vm.parent_id == "vcenter:vc01"
    p = vm.properties
    assert p["template"] is True
    assert p["host"] is None
    assert p["resourcePool"] is None and p["folder"] is None
    assert p["disks"] is None and p["nics"] is None  # no config returned: unknown
    assert vm.relationships == []


def test_vm_folder_is_only_a_folder():
    # app02 sits directly in the datacenter's "vm" root folder
    assert _by_id(normalize(_inventory()))["vm:vc01:vm-103"].properties["folder"] == "vm"


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
    assert p["hosts"] == ["esx01.lab.local", "esx02.lab.local"]
    assert p["multipleHostAccess"] is True
    assert p["maintenanceMode"] == "normal"
    assert p["vmfsVersion"] is None
    nfs = res["datastore:vc01:datastore-16"].properties
    assert nfs["accessible"] is False
    assert nfs["hosts"] == []
    assert nfs["multipleHostAccess"] is None and nfs["maintenanceMode"] is None
    local = res["datastore:vc01:datastore-17"].properties
    assert local["vmfsVersion"] == "6.82"
    assert local["multipleHostAccess"] is False
    assert local["hosts"] == ["esx01.lab.local"]


def test_network_types():
    res = _by_id(normalize(_inventory()))
    assert res["network:vc01:dvportgroup-20"].properties["type"] == "dvportgroup"
    assert res["network:vc01:network-30"].properties["type"] == "standard"
    assert res["network:vc01:opaquenetwork-40"].properties["type"] == "opaque"
    assert all(res[k].properties["exists"] is True for k in res if k.startswith("network:"))


def test_network_deep_properties():
    res = _by_id(normalize(_inventory()))
    mgmt = res["network:vc01:dvportgroup-20"].properties
    assert mgmt["vlan"] == 10
    assert mgmt["numPorts"] == 8
    assert mgmt["switch"] == "dvs-wld01"
    assert mgmt["hosts"] is None
    trunk = res["network:vc01:dvportgroup-21"].properties
    assert trunk["vlan"] == "trunk 5,100-110,200-205"
    assert trunk["numPorts"] == 128
    assert res["network:vc01:dvportgroup-22"].properties["vlan"] == "pvlan 301"
    std = res["network:vc01:network-30"].properties
    assert std["hosts"] == ["esx-standalone.lab.local", "esx01.lab.local"]
    assert std["vlan"] is None and std["switch"] is None and std["numPorts"] is None
    opaque = res["network:vc01:opaquenetwork-40"].properties
    assert opaque["opaqueNetworkType"] == "nsx.LogicalSwitch"
    assert opaque["hosts"] is None


def test_format_vlan():
    assert format_vlan({"kind": "id", "vlanId": 0}) == 0
    assert format_vlan({"kind": "trunk", "ranges": [[0, 4094]]}) == "trunk 0-4094"
    assert format_vlan({"kind": "trunk", "ranges": []}) == "trunk"
    assert format_vlan({"kind": "pvlan", "pvlanId": 7}) == "pvlan 7"
    assert format_vlan(None) is None
    assert format_vlan({"kind": "weird"}) is None


def test_property_specs_cover_network_subclasses():
    # Every network kind we classify must be reachable through the base view.
    assert "Network" in PROPERTY_SPECS
    assert set(NETWORK_TYPES) >= {"Network", "DistributedVirtualPortgroup", "OpaqueNetwork"}
    # and the subclass-only paths come from their own views
    assert "config.defaultPortConfig" in PROPERTY_SPECS["DistributedVirtualPortgroup"]
    assert "summary" in PROPERTY_SPECS["OpaqueNetwork"]


def test_contract_keys_present_per_type():
    """Every key in docs/PROPERTIES.md is emitted for its type."""
    contract = {
        "vcenter": "name version build apiVersion instanceUuid osType",
        "cluster": (
            "hostCount hosts drsEnabled drsAutomationLevel haEnabled haAdmissionControl "
            "evcMode vsanEnabled ruleCount totalCpuMhz totalMemoryBytes numVms overallStatus"
        ),
        "host": (
            "connectionState powerState maintenanceMode cluster datacenter version build "
            "model vendor biosVersion cpuMhz numCpuCores memoryBytes uptimeSeconds bootTime "
            "lockdownMode ntpServers dnsServers vmkernelAdapters physicalNics "
            "standardSwitches numVms datastores overallStatus"
        ),
        "vm": (
            "powerState connectionState host cluster resourcePool folder guestFullName "
            "guestHostname guestIp guestState toolsStatus toolsVersion numCpu memoryMB "
            "hardwareVersion template cpuReservationMhz memReservationMB annotation "
            "snapshotCount oldestSnapshotTime disks nics networks datastores "
            "storageCommittedBytes bootTime overallStatus"
        ),
        "datastore": (
            "capacity freeSpace accessible type url hosts multipleHostAccess "
            "maintenanceMode vmfsVersion overallStatus"
        ),
        "network": "type vlan numPorts switch hosts exists",
    }
    by_type: dict[str, list[Resource]] = {}
    for r in normalize(_inventory()):
        by_type.setdefault(r.type, []).append(r)
    for rtype, keys in contract.items():
        for r in by_type[rtype]:
            missing = set(keys.split()) - set(r.properties)
            assert not missing, f"{r.id} missing {sorted(missing)}"
