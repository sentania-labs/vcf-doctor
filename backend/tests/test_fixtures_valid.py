"""Validate the demo fixtures in fixtures/ against the frozen Resource model.

Checks structure (every resource parses, ids unique, parent and relationship
targets resolve) and asserts the exact A -> B delta by comparing tracked
properties directly. The diff engine is deliberately not imported here so a
diff bug cannot mask a fixture bug or vice versa.
"""

import json
from pathlib import Path

import pytest

from app.models.resource import Resource

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

VC = "vc-wld01"
HOST_ESX03 = f"host:{VC}:esx03"
HOST_ESX07 = f"host:{VC}:esx07"
HOST_ESX02 = f"host:{VC}:esx02"
HOST_ESX04 = f"host:{VC}:esx04"
VM_APP02 = f"vm:{VC}:app02"
VM_WEB03 = f"vm:{VC}:web03"
VM_DMZ_LB01 = f"vm:{VC}:dmz-lb01"
VM_DMZ_JUMP01 = f"vm:{VC}:dmz-jump01"
DS_VSAN01 = f"datastore:{VC}:wld01-cl01-vsan01"
NET_DMZ = f"network:{VC}:seg-dmz-10.20.40.0"
NET_DMZ_NAME = "seg-dmz-10.20.40.0"

# Resources whose content is allowed to differ between A and B.
EXPECTED_CHANGED_IDS = {
    HOST_ESX03,
    HOST_ESX07,
    VM_APP02,
    VM_WEB03,
    VM_DMZ_LB01,
    VM_DMZ_JUMP01,
    DS_VSAN01,
}
EXPECTED_REMOVED_IDS = {NET_DMZ}

TRACKED = {
    "host": ["connectionState", "powerState", "maintenanceMode", "cluster"],
    "vm": ["powerState", "host", "networks", "datastores"],
    "datastore": ["accessible", "capacity", "freeSpace"],
    "cluster": ["hostCount"],
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
def test_property_contract(name: str) -> None:
    _, resources = load(name)
    by_id = index(resources)
    names = {r.id: r.name for r in resources}
    for r in resources:
        p = r.properties
        if r.type == "host":
            assert p["connectionState"] in {"connected", "disconnected", "notResponding"}
            assert isinstance(p["maintenanceMode"], bool)
            for key in ("cpuMhz", "numCpuCores", "memoryBytes"):
                assert isinstance(p[key], int)
            assert by_id[r.parent_id].name == p["cluster"]
        elif r.type == "vm":
            host = by_id[r.parent_id]
            assert host.type == "host" and host.name == p["host"]
            assert by_id[host.parent_id].name == p["cluster"]
            assert isinstance(p["template"], bool)
            runs_on = [x.target_id for x in r.relationships if x.kind == "runs_on"]
            assert runs_on == [r.parent_id]
            nets = [names[x.target_id] for x in r.relationships if x.kind == "uses_network"]
            assert nets == p["networks"]
            dss = [names[x.target_id] for x in r.relationships if x.kind == "uses_datastore"]
            assert dss == p["datastores"]
        elif r.type == "datastore":
            assert isinstance(p["capacity"], int) and isinstance(p["freeSpace"], int)
            assert 0 <= p["freeSpace"] <= p["capacity"]
            assert isinstance(p["accessible"], bool)
        elif r.type == "cluster":
            members = [x for x in resources if x.type == "host" and x.parent_id == r.id]
            assert p["hostCount"] == len(members)


def test_inventory_shape(snap_a: list[Resource]) -> None:
    counts: dict[str, int] = {}
    for r in snap_a:
        counts[r.type] = counts.get(r.type, 0) + 1
    assert counts == {
        "vcenter": 1,
        "datacenter": 1,
        "cluster": 2,
        "host": 7,
        "vm": 30,
        "datastore": 4,
        "network": 6,
    }


def test_exact_delta(snap_a: list[Resource], snap_b: list[Resource]) -> None:
    a, b = index(snap_a), index(snap_b)

    assert set(a) - set(b) == EXPECTED_REMOVED_IDS
    assert set(b) - set(a) == set()

    # Everything not on the expected list is byte-identical.
    for rid in set(a) & set(b):
        if rid not in EXPECTED_CHANGED_IDS:
            assert a[rid].model_dump() == b[rid].model_dump(), f"unexpected change in {rid}"

    def tracked_diff(rid: str) -> dict[str, tuple[object, object]]:
        pa, pb = a[rid].properties, b[rid].properties
        return {
            k: (pa.get(k), pb.get(k)) for k in TRACKED[a[rid].type] if pa.get(k) != pb.get(k)
        }

    # esx03: connected -> disconnected, nothing else.
    assert tracked_diff(HOST_ESX03) == {"connectionState": ("connected", "disconnected")}

    # esx07: maintenance mode on.
    assert tracked_diff(HOST_ESX07) == {"maintenanceMode": (False, True)}

    # app02: esx02 -> esx04, with parent and runs_on following.
    assert tracked_diff(VM_APP02) == {"host": (a[HOST_ESX02].name, a[HOST_ESX04].name)}
    assert a[VM_APP02].parent_id == HOST_ESX02
    assert b[VM_APP02].parent_id == HOST_ESX04
    assert [x.target_id for x in b[VM_APP02].relationships if x.kind == "runs_on"] == [HOST_ESX04]

    # web03: powered off.
    assert tracked_diff(VM_WEB03) == {"powerState": ("poweredOn", "poweredOff")}

    # vsan01: freeSpace drops so usage passes 90%.
    diff = tracked_diff(DS_VSAN01)
    assert set(diff) == {"freeSpace"}
    cap = b[DS_VSAN01].properties["capacity"]
    used_a = 1 - a[DS_VSAN01].properties["freeSpace"] / cap
    used_b = 1 - b[DS_VSAN01].properties["freeSpace"] / cap
    assert used_a < 0.90
    assert 0.90 < used_b < 0.92

    # DMZ segment removed, and only the two DMZ VMs lose it from their lists.
    assert NET_DMZ not in b
    for vm_id in (VM_DMZ_LB01, VM_DMZ_JUMP01):
        assert NET_DMZ_NAME in a[vm_id].properties["networks"]
        assert NET_DMZ_NAME not in b[vm_id].properties["networks"]
        expected = [n for n in a[vm_id].properties["networks"] if n != NET_DMZ_NAME]
        assert tracked_diff(vm_id) == {"networks": (a[vm_id].properties["networks"], expected)}
        assert all(x.target_id != NET_DMZ for x in b[vm_id].relationships)
    other_users = [
        r.id
        for r in snap_a
        if r.type == "vm"
        and NET_DMZ_NAME in r.properties["networks"]
        and r.id not in (VM_DMZ_LB01, VM_DMZ_JUMP01)
    ]
    assert other_users == []


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
