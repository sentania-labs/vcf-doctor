"""Validate the test fixtures in fixtures/ against the frozen Resource model
and the property contract in docs/PROPERTIES.md.

Checks structure (every resource parses, ids unique, parent and relationship
targets resolve), asserts every resource carries every contract key for its
type, and asserts the exact A -> B delta by comparing properties directly. The
diff engine is deliberately not imported here so a diff bug cannot mask a
fixture bug or vice versa.
"""

import json
from pathlib import Path

import pytest

from app.models.resource import Resource

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

VC = "vc-wld01"
DOMAIN = "wld01.vcf.example"
HOST_ESX02 = f"host:{VC}:esx02"
HOST_ESX03 = f"host:{VC}:esx03"
HOST_ESX04 = f"host:{VC}:esx04"
HOST_ESX07 = f"host:{VC}:esx07"
VM_APP01 = f"vm:{VC}:app01"
VM_APP02 = f"vm:{VC}:app02"
VM_DB01 = f"vm:{VC}:db01"
VM_WEB02 = f"vm:{VC}:web02"
VM_WEB03 = f"vm:{VC}:web03"
VM_DMZ_LB01 = f"vm:{VC}:dmz-lb01"
VM_DMZ_JUMP01 = f"vm:{VC}:dmz-jump01"
VM_BACKUP_PROXY01 = f"vm:{VC}:backup-proxy01"
VM_MONITORING01 = f"vm:{VC}:monitoring01"
DS_VSAN01 = f"datastore:{VC}:wld01-cl01-vsan01"
NET_DMZ = f"network:{VC}:seg-dmz-10.20.40.0"
NET_DMZ_NAME = "seg-dmz-10.20.40.0"
NET_VMOTION = f"network:{VC}:pg-vmotion"
CLUSTER_EDGE = f"cluster:{VC}:wld01-edge"

# Resources whose content is allowed to differ between A and B.
EXPECTED_CHANGED_IDS = {
    HOST_ESX02,
    HOST_ESX03,
    HOST_ESX04,
    HOST_ESX07,
    VM_APP01,
    VM_APP02,
    VM_DB01,
    VM_WEB02,
    VM_WEB03,
    VM_DMZ_LB01,
    VM_DMZ_JUMP01,
    DS_VSAN01,
    NET_VMOTION,
    CLUSTER_EDGE,
}
EXPECTED_REMOVED_IDS = {NET_DMZ}

# Every key docs/PROPERTIES.md lists per type. Every fixture resource must
# carry every key (null is allowed, absence is not).
CONTRACT_KEYS: dict[str, set[str]] = {
    "vcenter": {"name", "version", "build", "apiVersion", "instanceUuid", "osType"},
    "datacenter": set(),
    "cluster": {
        "hostCount",
        "hosts",
        "drsEnabled",
        "drsAutomationLevel",
        "haEnabled",
        "haAdmissionControl",
        "evcMode",
        "vsanEnabled",
        "ruleCount",
        "totalCpuMhz",
        "totalMemoryBytes",
        "numVms",
        "overallStatus",
    },
    "host": {
        "connectionState",
        "powerState",
        "maintenanceMode",
        "cluster",
        "datacenter",
        "version",
        "build",
        "model",
        "vendor",
        "biosVersion",
        "cpuMhz",
        "numCpuCores",
        "memoryBytes",
        "uptimeSeconds",
        "bootTime",
        "lockdownMode",
        "ntpServers",
        "dnsServers",
        "vmkernelAdapters",
        "physicalNics",
        "standardSwitches",
        "numVms",
        "datastores",
        "overallStatus",
    },
    "vm": {
        "powerState",
        "connectionState",
        "host",
        "cluster",
        "resourcePool",
        "folder",
        "guestFullName",
        "guestHostname",
        "guestIp",
        "guestState",
        "toolsStatus",
        "toolsVersion",
        "numCpu",
        "memoryMB",
        "hardwareVersion",
        "template",
        "cpuReservationMhz",
        "memReservationMB",
        "annotation",
        "snapshotCount",
        "oldestSnapshotTime",
        "disks",
        "nics",
        "networks",
        "datastores",
        "storageCommittedBytes",
        "bootTime",
        "overallStatus",
    },
    "datastore": {
        "capacity",
        "freeSpace",
        "accessible",
        "type",
        "url",
        "hosts",
        "multipleHostAccess",
        "maintenanceMode",
        "vmfsVersion",
        "overallStatus",
    },
    "network": {"type", "vlan", "numPorts", "switch", "hosts", "exists"},
}


def load(name: str) -> tuple[str, list[Resource]]:
    data = json.loads((FIXTURES / name).read_text())
    assert set(data) == {"label", "resources"}
    return data["label"], [Resource.model_validate(r) for r in data["resources"]]


@pytest.fixture(scope="module")
def snap_a() -> list[Resource]:
    return load("snapshot_a.json")[1]


@pytest.fixture(scope="module")
def snap_b() -> list[Resource]:
    return load("snapshot_b.json")[1]


def index(resources: list[Resource]) -> dict[str, Resource]:
    return {r.id: r for r in resources}


@pytest.mark.parametrize("name", ["snapshot_a.json", "snapshot_b.json"])
def test_structure(name: str) -> None:
    label, resources = load(name)
    assert label
    ids = [r.id for r in resources]
    assert len(ids) == len(set(ids)), "duplicate resource ids"
    by_id = index(resources)
    for r in resources:
        assert r.source == f"vcenter:{VC}"
        assert r.id.startswith(f"{r.type}:{VC}:")
        if r.type == "vcenter":
            assert r.parent_id is None
        else:
            assert r.parent_id in by_id, f"{r.id} parent {r.parent_id} missing"
        for rel in r.relationships:
            assert rel.target_id in by_id, f"{r.id} -> {rel.target_id} missing"
            assert rel.kind in {"runs_on", "uses_network", "uses_datastore", "member_of"}


@pytest.mark.parametrize("name", ["snapshot_a.json", "snapshot_b.json"])
def test_every_resource_has_every_contract_key(name: str) -> None:
    _, resources = load(name)
    for r in resources:
        assert r.type in CONTRACT_KEYS, f"{r.id}: unknown type {r.type}"
        missing = CONTRACT_KEYS[r.type] - set(r.properties)
        assert not missing, f"{r.id} missing contract keys {sorted(missing)}"


@pytest.mark.parametrize("name", ["snapshot_a.json", "snapshot_b.json"])
def test_property_contract(name: str) -> None:
    _, resources = load(name)
    by_id = index(resources)
    names = {r.id: r.name for r in resources}
    host_names = {r.name for r in resources if r.type == "host"}
    network_names = {r.name for r in resources if r.type == "network"}
    datastore_names = {r.name for r in resources if r.type == "datastore"}
    for r in resources:
        p = r.properties
        if r.type == "vcenter":
            assert p["version"] == "8.0.3"
            assert isinstance(p["instanceUuid"], str)
        elif r.type == "host":
            assert p["connectionState"] in {"connected", "disconnected", "notResponding"}
            assert isinstance(p["maintenanceMode"], bool)
            for key in ("cpuMhz", "numCpuCores", "memoryBytes", "uptimeSeconds", "numVms"):
                assert isinstance(p[key], int)
            assert by_id[r.parent_id].name == p["cluster"]
            assert p["version"] == "8.0.3" and p["build"] == "24022510"
            assert p["model"] == "PowerEdge R760" and p["vendor"] == "Dell Inc."
            assert p["lockdownMode"] == "lockdownNormal"
            assert p["ntpServers"] and p["dnsServers"]
            assert p["bootTime"].endswith("Z")
            vmks = {v["device"]: v for v in p["vmkernelAdapters"]}
            assert set(vmks) == {"vmk0", "vmk1", "vmk2"}
            assert vmks["vmk0"]["mtu"] == 1500
            for v in vmks.values():
                assert set(v) == {"device", "ip", "mtu", "portgroup"}
                assert v["portgroup"] in network_names
            assert [n["device"] for n in p["physicalNics"]] == ["vmnic0", "vmnic1"]
            for n in p["physicalNics"]:
                assert set(n) == {"device", "mac", "linkSpeedMb"}
                assert n["linkSpeedMb"] == 25000
            assert p["standardSwitches"] == []
            assert set(p["datastores"]) <= datastore_names
            uses = {names[x.target_id] for x in r.relationships if x.kind == "uses_datastore"}
            assert uses == set(p["datastores"])
            resident = [x for x in resources if x.type == "vm" and x.parent_id == r.id]
            assert p["numVms"] == len(resident)
        elif r.type == "vm":
            host = by_id[r.parent_id]
            assert host.type == "host" and host.name == p["host"]
            assert by_id[host.parent_id].name == p["cluster"]
            assert isinstance(p["template"], bool)
            runs_on = [x.target_id for x in r.relationships if x.kind == "runs_on"]
            assert runs_on == [r.parent_id]
            nets = [names[x.target_id] for x in r.relationships if x.kind == "uses_network"]
            assert nets == p["networks"] == sorted(p["networks"])
            dss = [names[x.target_id] for x in r.relationships if x.kind == "uses_datastore"]
            assert dss == p["datastores"]
            assert p["hardwareVersion"] == "vmx-21"
            assert p["powerState"] in {"poweredOn", "poweredOff"}
            powered_on = p["powerState"] == "poweredOn"
            assert p["guestState"] == ("running" if powered_on else "notRunning")
            assert p["toolsStatus"] == ("toolsOk" if powered_on else "toolsNotRunning")
            assert (p["bootTime"] is None) == (not powered_on)
            assert p["disks"], f"{r.id} has no disks"
            for i, d in enumerate(p["disks"]):
                assert set(d) == {"label", "capacityBytes", "datastore", "thin"}
                assert d["label"] == f"Hard disk {i + 1}"
                assert d["datastore"] in p["datastores"]
                assert isinstance(d["capacityBytes"], int)
            # A nic can outlive its network (B's DMZ VMs), never the reverse.
            assert set(p["networks"]) <= {n["network"] for n in p["nics"]}
            for i, n in enumerate(p["nics"]):
                assert set(n) == {"label", "mac", "network", "connected"}
                assert n["label"] == f"Network adapter {i + 1}"
                assert n["connected"] is powered_on
            assert isinstance(p["snapshotCount"], int)
            assert (p["oldestSnapshotTime"] is None) == (p["snapshotCount"] == 0)
            assert isinstance(p["storageCommittedBytes"], int)
            assert p["storageCommittedBytes"] <= sum(d["capacityBytes"] for d in p["disks"])
        elif r.type == "datastore":
            assert isinstance(p["capacity"], int) and isinstance(p["freeSpace"], int)
            assert 0 <= p["freeSpace"] <= p["capacity"]
            assert isinstance(p["accessible"], bool)
            assert p["type"] in {"vsan", "VMFS", "NFS41"}
            assert (p["vmfsVersion"] is None) == (p["type"] != "VMFS")
            assert p["hosts"] and set(p["hosts"]) <= host_names
            assert p["hosts"] == sorted(p["hosts"])
            assert p["url"].startswith("ds:///vmfs/volumes/")
        elif r.type == "network":
            assert p["exists"] is True
            if p["type"] == "dvportgroup":
                assert p["switch"] == "wld01-vds01"
                assert isinstance(p["vlan"], int) or str(p["vlan"]).startswith("trunk ")
                assert isinstance(p["numPorts"], int)
            else:
                assert p["type"] == "opaque"
                assert p["vlan"] is None and p["switch"] is None
            assert p["hosts"] is None, "hosts is for standard switches only"
        elif r.type == "cluster":
            members = [x for x in resources if x.type == "host" and x.parent_id == r.id]
            assert p["hostCount"] == len(members)
            assert p["hosts"] == sorted(m.id for m in members)
            assert p["drsEnabled"] is True and p["haEnabled"] is True
            assert p["drsAutomationLevel"] in {"manual", "partiallyAutomated", "fullyAutomated"}
            assert p["haAdmissionControl"] is True
            assert p["evcMode"] == "intel-sapphirerapids"
            assert p["ruleCount"] == 2
            assert p["totalCpuMhz"] == sum(
                m.properties["cpuMhz"] * m.properties["numCpuCores"] for m in members
            )
            assert p["totalMemoryBytes"] == sum(m.properties["memoryBytes"] for m in members)
            assert p["numVms"] == sum(m.properties["numVms"] for m in members)


def test_inventory_shape(snap_a: list[Resource]) -> None:
    counts: dict[str, int] = {}
    for r in snap_a:
        counts[r.type] = counts.get(r.type, 0) + 1
    assert counts == {
        "vcenter": 1,
        "datacenter": 1,
        "cluster": 2,
        "host": 7,
        "vm": 28,
        "datastore": 4,
        "network": 8,
    }
    assert sum(counts.values()) == 51


def test_inventory_facts(snap_a: list[Resource]) -> None:
    a = index(snap_a)
    assert a[f"cluster:{VC}:wld01-cl01"].properties["vsanEnabled"] is True
    assert a[f"cluster:{VC}:wld01-edge"].properties["vsanEnabled"] is False
    assert a[NET_VMOTION].properties["vlan"] == 200
    assert a[f"network:{VC}:pg-vsan"].properties["vlan"] == 300
    assert a[f"datastore:{VC}:nfs01-iso-templates"].properties["vmfsVersion"] is None
    assert a[f"datastore:{VC}:nfs01-iso-templates"].properties["type"] == "NFS41"
    assert a[DS_VSAN01].properties["type"] == "vsan"
    for h in (r for r in snap_a if r.type == "host"):
        p = h.properties
        assert p["ntpServers"] == [f"ntp1.{DOMAIN}", f"ntp2.{DOMAIN}"]
        vmks = {v["device"]: v for v in p["vmkernelAdapters"]}
        assert vmks["vmk1"]["mtu"] == 9000 and vmks["vmk1"]["portgroup"] == "pg-vmotion"
        assert vmks["vmk2"]["mtu"] == 9000 and vmks["vmk2"]["portgroup"] == "pg-vsan"
    # Snapshot outliers: one VM over the count threshold, one over the age threshold.
    many = a[VM_BACKUP_PROXY01].properties
    assert many["snapshotCount"] == 4
    assert many["oldestSnapshotTime"] == "2026-08-29T06:00:00Z"
    old = a[VM_MONITORING01].properties
    assert old["snapshotCount"] == 1
    assert old["oldestSnapshotTime"] == "2026-08-10T06:00:00Z"
    others = [
        r.id
        for r in snap_a
        if r.type == "vm"
        and r.properties["snapshotCount"] > 0
        and r.id not in (VM_BACKUP_PROXY01, VM_MONITORING01)
    ]
    assert others == []
    # Nothing in A trips the empty-NTP, HA-off, DRS-off or version-mismatch checks.
    assert all(r.properties["ntpServers"] for r in snap_a if r.type == "host")
    assert (
        len({(r.properties["version"], r.properties["build"]) for r in snap_a if r.type == "host"})
        == 1
    )
    assert all(
        r.properties["toolsStatus"] == "toolsOk"
        for r in snap_a
        if r.type == "vm" and r.properties["powerState"] == "poweredOn"
    )


def prop_diff(a: Resource, b: Resource) -> dict[str, tuple[object, object]]:
    keys = set(a.properties) | set(b.properties)
    return {
        k: (a.properties.get(k), b.properties.get(k))
        for k in sorted(keys)
        if a.properties.get(k) != b.properties.get(k)
    }


def test_exact_delta(snap_a: list[Resource], snap_b: list[Resource]) -> None:
    a, b = index(snap_a), index(snap_b)

    assert set(a) - set(b) == EXPECTED_REMOVED_IDS
    assert set(b) - set(a) == set()

    # Everything not on the expected list is byte-identical.
    for rid in set(a) & set(b):
        if rid not in EXPECTED_CHANGED_IDS:
            assert a[rid].model_dump() == b[rid].model_dump(), f"unexpected change in {rid}"

    # Only web02 is renamed; every other id keeps its name.
    for rid in set(a) & set(b):
        if rid != VM_WEB02:
            assert a[rid].name == b[rid].name, f"unexpected rename of {rid}"

    # esx03: connected -> disconnected, nothing else (high).
    assert prop_diff(a[HOST_ESX03], b[HOST_ESX03]) == {
        "connectionState": ("connected", "disconnected")
    }

    # esx07: maintenance mode on (medium).
    assert prop_diff(a[HOST_ESX07], b[HOST_ESX07]) == {"maintenanceMode": (False, True)}

    # esx02: vmk1 MTU 9000 -> 1500 (high); numVms drops because app02 left.
    d = prop_diff(a[HOST_ESX02], b[HOST_ESX02])
    assert set(d) == {"vmkernelAdapters", "numVms"}
    assert d["numVms"] == (6, 5)
    old_vmk = {v["device"]: v for v in d["vmkernelAdapters"][0]}
    new_vmk = {v["device"]: v for v in d["vmkernelAdapters"][1]}
    assert old_vmk["vmk1"]["mtu"] == 9000 and new_vmk["vmk1"]["mtu"] == 1500
    for dev in ("vmk0", "vmk2"):
        assert old_vmk[dev] == new_vmk[dev]
    assert {k: v for k, v in old_vmk["vmk1"].items() if k != "mtu"} == {
        k: v for k, v in new_vmk["vmk1"].items() if k != "mtu"
    }

    # esx04: loses ntp2 (medium); numVms grows because app02 arrived.
    d = prop_diff(a[HOST_ESX04], b[HOST_ESX04])
    assert d == {
        "ntpServers": ([f"ntp1.{DOMAIN}", f"ntp2.{DOMAIN}"], [f"ntp1.{DOMAIN}"]),
        "numVms": (4, 5),
    }

    # pg-vmotion: VLAN 200 -> 201 (high).
    assert prop_diff(a[NET_VMOTION], b[NET_VMOTION]) == {"vlan": (200, 201)}

    # wld01-edge: DRS fullyAutomated -> manual (medium).
    assert prop_diff(a[CLUSTER_EDGE], b[CLUSTER_EDGE]) == {
        "drsAutomationLevel": ("fullyAutomated", "manual")
    }

    # app02: esx02 -> esx04, with parent and runs_on following (low).
    assert prop_diff(a[VM_APP02], b[VM_APP02]) == {"host": (a[HOST_ESX02].name, a[HOST_ESX04].name)}
    assert a[VM_APP02].parent_id == HOST_ESX02
    assert b[VM_APP02].parent_id == HOST_ESX04
    assert [x.target_id for x in b[VM_APP02].relationships if x.kind == "runs_on"] == [HOST_ESX04]

    # app01: gains Hard disk 3, 100 GiB (medium).
    d = prop_diff(a[VM_APP01], b[VM_APP01])
    assert set(d) == {"disks"}
    old_disks, new_disks = d["disks"]
    assert new_disks[: len(old_disks)] == old_disks
    assert new_disks[len(old_disks) :] == [
        {
            "label": "Hard disk 3",
            "capacityBytes": 100 * 1024**3,
            "datastore": "wld01-cl01-vsan01",
            "thin": True,
        }
    ]

    # db01: memory 32 GiB -> 48 GiB (low).
    assert prop_diff(a[VM_DB01], b[VM_DB01]) == {"memoryMB": (32768, 49152)}

    # web02: renamed, same id, properties untouched (medium).
    assert prop_diff(a[VM_WEB02], b[VM_WEB02]) == {}
    assert a[VM_WEB02].name == "web02" and b[VM_WEB02].name == "web02-old"

    # web03: powered off (medium); guest facts follow the power state.
    d = prop_diff(a[VM_WEB03], b[VM_WEB03])
    assert set(d) == {"powerState", "guestState", "guestIp", "toolsStatus", "bootTime", "nics"}
    assert d["powerState"] == ("poweredOn", "poweredOff")
    assert d["guestState"] == ("running", "notRunning")
    assert d["toolsStatus"] == ("toolsOk", "toolsNotRunning")
    assert d["guestIp"][1] is None and d["bootTime"][1] is None
    assert [n["connected"] for n in d["nics"][1]] == [False]

    # vsan01: freeSpace drops so usage passes 90% (medium, capacity warning).
    d = prop_diff(a[DS_VSAN01], b[DS_VSAN01])
    assert set(d) == {"freeSpace"}
    cap = b[DS_VSAN01].properties["capacity"]
    used_a = 1 - a[DS_VSAN01].properties["freeSpace"] / cap
    used_b = 1 - b[DS_VSAN01].properties["freeSpace"] / cap
    assert used_a < 0.90
    assert 0.90 < used_b < 0.92

    # DMZ segment removed (high), and only the two DMZ VMs lose it from their
    # network lists (medium). The nic entries keep naming the dead segment.
    assert NET_DMZ not in b
    for vm_id in (VM_DMZ_LB01, VM_DMZ_JUMP01):
        assert NET_DMZ_NAME in a[vm_id].properties["networks"]
        assert NET_DMZ_NAME not in b[vm_id].properties["networks"]
        expected = [n for n in a[vm_id].properties["networks"] if n != NET_DMZ_NAME]
        assert prop_diff(a[vm_id], b[vm_id]) == {
            "networks": (a[vm_id].properties["networks"], expected)
        }
        assert all(x.target_id != NET_DMZ for x in b[vm_id].relationships)
    other_users = [
        r.id
        for r in snap_a
        if r.type == "vm"
        and NET_DMZ_NAME in r.properties["networks"]
        and r.id not in (VM_DMZ_LB01, VM_DMZ_JUMP01)
    ]
    assert other_users == []

    # The snapshot outliers are untouched, so VM_SNAPSHOT_STALE fires in both.
    for vm_id in (VM_BACKUP_PROXY01, VM_MONITORING01):
        assert a[vm_id].model_dump() == b[vm_id].model_dump()


def test_generator_is_deterministic(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("fixgen", FIXTURES / "generate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    a = mod.build_snapshot_a()
    b = mod.build_snapshot_b(a)
    on_disk_a = json.loads((FIXTURES / "snapshot_a.json").read_text())["resources"]
    on_disk_b = json.loads((FIXTURES / "snapshot_b.json").read_text())["resources"]
    assert a == on_disk_a, "snapshot_a.json is stale; run python fixtures/generate.py"
    assert b == on_disk_b, "snapshot_b.json is stale; run python fixtures/generate.py"
