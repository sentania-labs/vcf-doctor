"""Semantic diff engine: classification and significance rules."""

from app.diff import diff
from app.models import Resource

SRC = "vcenter:vc01"


def cluster(name: str, **props) -> Resource:
    return Resource(
        id=f"cluster:vc01:{name}", type="cluster", name=name, source=SRC, properties=props
    )


def host(name: str, cl: str = "wld01", **props) -> Resource:
    base = {
        "connectionState": "connected",
        "powerState": "poweredOn",
        "maintenanceMode": False,
        "cluster": f"cluster:vc01:{cl}",
        "cpuMhz": 64000,
        "uptimeSeconds": 12345,
    }
    base.update(props)
    return Resource(
        id=f"host:vc01:{name}",
        type="host",
        name=name,
        source=SRC,
        parent_id=base["cluster"],
        properties=base,
    )


def vm(name: str, h: str = "esx01", **props) -> Resource:
    base = {
        "powerState": "poweredOn",
        "host": f"host:vc01:{h}",
        "networks": ["n1"],
        "datastores": ["d1"],
    }
    base.update(props)
    return Resource(id=f"vm:vc01:{name}", type="vm", name=name, source=SRC, properties=base)


def datastore(name: str, capacity: int = 1000, free: int = 500, **props) -> Resource:
    base = {"capacity": capacity, "freeSpace": free, "accessible": True}
    base.update(props)
    return Resource(id=f"ds:vc01:{name}", type="datastore", name=name, source=SRC, properties=base)


def network(name: str, **props) -> Resource:
    return Resource(id=f"net:vc01:{name}", type="network", name=name, source=SRC, properties=props)


def only(changes, rid):
    hits = [c for c in changes if c.resource_id == rid]
    assert len(hits) == 1, f"expected one change for {rid}, got {changes}"
    return hits[0]


def base() -> list[Resource]:
    return [
        cluster("wld01"),
        host("esx01"),
        host("esx02"),
        vm("web01"),
        datastore("vsan"),
        network("vlan10"),
    ]


# ------------------------------------------------------------------ basics


def test_identical_snapshots_produce_no_changes():
    assert diff(base(), base()) == []


def test_noisy_properties_are_ignored():
    new = base()
    new[1].properties["uptimeSeconds"] = 999999
    new[1].properties["cpuMhz"] = 65000
    new[3].properties["guestToolsVersion"] = "12.0"
    assert diff(base(), new) == []


def test_added_and_removed_classification():
    old = base()
    new = base() + [vm("new01")]
    new = [r for r in new if r.id != "net:vc01:vlan10"]
    changes = diff(old, new)
    added = only(changes, "vm:vc01:new01")
    assert added.change_type == "added" and added.significance == "low"
    removed = only(changes, "net:vc01:vlan10")
    assert removed.change_type == "removed" and removed.significance == "high"
    assert removed.resource_type == "network" and removed.resource_name == "vlan10"


# ------------------------------------------------------------------ hosts


def test_host_connection_state_change_is_high_with_arrow_summary():
    new = base()
    new[1].properties["connectionState"] = "disconnected"
    c = only(diff(base(), new), "host:vc01:esx01")
    assert c.change_type == "modified"
    assert c.significance == "high"
    assert c.property_changes["connectionState"].old == "connected"
    assert c.property_changes["connectionState"].new == "disconnected"
    assert c.summary == "connected -> disconnected"


def test_host_maintenance_mode_is_medium():
    new = base()
    new[1].properties["maintenanceMode"] = True
    c = only(diff(base(), new), "host:vc01:esx01")
    assert c.significance == "medium"
    assert c.property_changes["maintenanceMode"].new is True
    assert "maintenance" in c.summary


def test_host_removed_is_high():
    new = [r for r in base() if r.id != "host:vc01:esx02"]
    changes = diff(base(), new)
    c = only(changes, "host:vc01:esx02")
    assert c.change_type == "removed" and c.significance == "high"
    # Cluster membership change is reported too, as medium.
    cl = only(changes, "cluster:vc01:wld01")
    assert cl.significance == "medium"
    assert cl.property_changes["hosts"].old == ["host:vc01:esx01", "host:vc01:esx02"]
    assert cl.property_changes["hosts"].new == ["host:vc01:esx01"]
    assert "removed esx02" in cl.summary


def test_host_cluster_move_is_medium():
    old = base() + [cluster("wld02")]
    new = base() + [cluster("wld02")]
    new[2] = host("esx02", "wld02")
    changes = diff(old, new)
    c = only(changes, "host:vc01:esx02")
    assert c.significance == "medium"
    assert c.summary == "wld01 -> wld02"
    assert {x.resource_id for x in changes if x.resource_type == "cluster"} == {
        "cluster:vc01:wld01",
        "cluster:vc01:wld02",
    }


def test_cluster_membership_from_hosts_property():
    old = [cluster("mgmt", hosts=["host:vc01:a"])]
    new = [cluster("mgmt", hosts=["host:vc01:a", "host:vc01:b"])]
    c = only(diff(old, new), "cluster:vc01:mgmt")
    assert c.significance == "medium" and c.summary == "added b"


# ------------------------------------------------------------------ vms


def test_vm_migration_is_low():
    new = base()
    new[3] = vm("web01", "esx02")
    c = only(diff(base(), new), "vm:vc01:web01")
    assert c.significance == "low"
    assert c.summary == "esx01 -> esx02"
    assert c.property_changes["host"].old == "host:vc01:esx01"


def test_vm_power_change_is_medium():
    new = base()
    new[3].properties["powerState"] = "poweredOff"
    c = only(diff(base(), new), "vm:vc01:web01")
    assert c.significance == "medium"
    assert c.summary == "poweredOn -> poweredOff"


def test_vm_migration_plus_power_change_takes_highest_significance():
    new = base()
    new[3] = vm("web01", "esx02", powerState="poweredOff")
    c = only(diff(base(), new), "vm:vc01:web01")
    assert c.significance == "medium"
    assert set(c.property_changes) == {"host", "powerState"}


def test_vm_network_and_datastore_list_order_is_ignored():
    old = base() + [vm("db01", networks=["a", "b"], datastores=["x", "y"])]
    new = base() + [vm("db01", networks=["b", "a"], datastores=["y", "x"])]
    assert diff(old, new) == []


def test_vm_network_change_reported():
    new = base()
    new[3].properties["networks"] = ["n2"]
    c = only(diff(base(), new), "vm:vc01:web01")
    assert c.property_changes["networks"].old == ["n1"]
    assert "networks added n2" in c.summary and "removed n1" in c.summary


def test_vm_removed_is_medium():
    new = [r for r in base() if r.id != "vm:vc01:web01"]
    c = only(diff(base(), new), "vm:vc01:web01")
    assert c.change_type == "removed" and c.significance == "medium"


# ------------------------------------------------------------------ datastores


def test_datastore_inaccessible_is_high():
    new = base()
    new[4].properties["accessible"] = False
    c = only(diff(base(), new), "ds:vc01:vsan")
    assert c.significance == "high"
    assert c.summary == "became inaccessible"


def test_datastore_removed_is_high():
    new = [r for r in base() if r.id != "ds:vc01:vsan"]
    c = only(diff(base(), new), "ds:vc01:vsan")
    assert c.change_type == "removed" and c.significance == "high"


def test_datastore_small_free_space_drift_is_ignored():
    old = [datastore("vsan", 1000, 500)]  # 50%
    new = [datastore("vsan", 1000, 470)]  # 53%, under 5 points, no band crossed
    assert diff(old, new) == []


def test_datastore_free_space_five_point_move_is_reported():
    old = [datastore("vsan", 1000, 500)]  # 50%
    new = [datastore("vsan", 1000, 440)]  # 56%
    c = only(diff(old, new), "ds:vc01:vsan")
    assert c.significance == "medium"
    assert c.property_changes["freeSpace"].old == 500
    assert c.property_changes["usagePct"].new == 56.0
    assert c.summary == "usage 50.0% -> 56.0%"


def test_datastore_free_space_crossing_85_is_reported_even_if_small():
    old = [datastore("vsan", 1000, 160)]  # 84%
    new = [datastore("vsan", 1000, 140)]  # 86%
    c = only(diff(old, new), "ds:vc01:vsan")
    assert c.significance == "medium"


def test_datastore_free_space_crossing_95_is_high():
    old = [datastore("vsan", 1000, 60)]  # 94%
    new = [datastore("vsan", 1000, 40)]  # 96%
    c = only(diff(old, new), "ds:vc01:vsan")
    assert c.significance == "high"


def test_datastore_capacity_change_is_low():
    old = [datastore("vsan", 1000, 500)]
    new = [
        datastore("vsan", 2000, 1500)
    ]  # 50% -> 25%: usage moved too, so freeSpace is also reported
    c = only(diff(old, new), "ds:vc01:vsan")
    assert c.property_changes["capacity"].old == 1000
    assert c.property_changes["capacity"].new == 2000
    old2 = [datastore("vsan", 1000, 500)]
    new2 = [datastore("vsan", 1010, 505)]  # capacity only, usage unchanged
    c2 = only(diff(old2, new2), "ds:vc01:vsan")
    assert set(c2.property_changes) == {"capacity"}
    assert c2.significance == "low"


# ------------------------------------------------------------------ networks / other


def test_network_property_changes_are_ignored_only_existence_matters():
    old = [network("vlan10", vlan=10)]
    new = [network("vlan10", vlan=20)]
    assert diff(old, new) == []


def test_other_added_is_low():
    old: list[Resource] = []
    new = [Resource(id="dc:vc01:dc1", type="datacenter", name="dc1", source=SRC)]
    c = only(diff(old, new), "dc:vc01:dc1")
    assert c.change_type == "added" and c.significance == "low"


def test_other_removed_is_low():
    old = [Resource(id="dc:vc01:dc1", type="datacenter", name="dc1", source=SRC)]
    c = only(diff(old, []), "dc:vc01:dc1")
    assert c.change_type == "removed" and c.significance == "low"


# ------------------------------------------------------------------ ordering


def test_output_sorted_high_first_then_type_then_name():
    old = base() + [vm("a01"), vm("b01"), host("esx03")]
    new = base() + [vm("a01", "esx02"), vm("b01", powerState="poweredOff"), host("esx03")]
    new[1].properties["connectionState"] = "disconnected"
    new[2].properties["maintenanceMode"] = True
    changes = diff(old, new)
    keys = [
        ({"high": 0, "medium": 1, "low": 2}[c.significance], c.resource_type, c.resource_name)
        for c in changes
    ]
    assert keys == sorted(keys)
    assert changes[0].resource_id == "host:vc01:esx01"
    assert changes[-1].resource_id == "vm:vc01:a01"
