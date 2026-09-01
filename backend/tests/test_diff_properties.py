"""Diff engine: the per-type significance table from docs/PROPERTIES.md, list
summaries, the min_significance filter, and resilience to odd value shapes."""

import pytest

from app.diff import diff
from app.diff.engine import DICT_LISTS, SCALAR_LISTS, SIGNIFICANCE
from app.models import Resource

SRC = "vcenter:vc01"


def res(rtype: str, name: str, **props) -> Resource:
    return Resource(id=f"{rtype}:vc01:{name}", type=rtype, name=name, source=SRC, properties=props)


def one(old: Resource, new: Resource):
    changes = diff([old], [new])
    assert len(changes) == 1, changes
    return changes[0]


# ------------------------------------------------------------ the table itself

EXPECTED = {
    "host": {
        "high": {"connectionState", "powerState", "vmkernelAdapters"},
        "medium": {
            "maintenanceMode",
            "cluster",
            "version",
            "build",
            "lockdownMode",
            "ntpServers",
            "dnsServers",
        },
        "low": {"model", "memoryBytes", "numCpuCores", "physicalNics", "standardSwitches"},
    },
    "vm": {
        "high": set(),
        "medium": {"powerState", "networks", "datastores", "disks", "nics"},
        "low": {
            "host",
            "numCpu",
            "memoryMB",
            "hardwareVersion",
            "template",
            "snapshotCount",
            "resourcePool",
            "folder",
            "cpuReservationMhz",
            "memReservationMB",
            "toolsStatus",
            "guestIp",
            "annotation",
        },
    },
    "cluster": {
        "high": {"drsEnabled", "haEnabled", "vsanEnabled"},
        "medium": {"hosts", "drsAutomationLevel", "haAdmissionControl", "evcMode"},
        "low": {"ruleCount"},
    },
    "datastore": {
        "high": {"accessible"},
        "medium": {"capacity", "freeSpace", "hosts", "maintenanceMode"},
        "low": {"multipleHostAccess"},
    },
    "network": {"high": {"vlan"}, "medium": {"switch"}, "low": {"numPorts", "hosts"}},
    "vcenter": {"high": set(), "medium": {"version", "build"}, "low": {"apiVersion"}},
}


def test_significance_table_matches_contract():
    for rtype, by_sig in EXPECTED.items():
        got = {s: {p for p, v in SIGNIFICANCE[rtype].items() if v == s} for s in by_sig}
        assert got == by_sig, rtype
    assert set(SIGNIFICANCE) == set(EXPECTED)


# Every scalar rule in the table, driven from the table: old -> new is
# reported with the tabled significance and a labeled arrow summary.
SCALAR_CASES = [
    (rtype, prop, sig)
    for rtype, props in SIGNIFICANCE.items()
    for prop, sig in props.items()
    if prop not in SCALAR_LISTS and prop not in DICT_LISTS and prop != "freeSpace"
]


@pytest.mark.parametrize(("rtype", "prop", "sig"), SCALAR_CASES)
def test_scalar_property_significance(rtype, prop, sig):
    if rtype == "host" and prop == "cluster":
        old, new = res(rtype, "x", cluster="a"), res(rtype, "x", cluster="b")
    elif prop in ("maintenanceMode",):
        old, new = res(rtype, "x", **{prop: False}), res(rtype, "x", **{prop: True})
    elif prop == "accessible":
        old, new = res(rtype, "x", accessible=True), res(rtype, "x", accessible=False)
    else:
        old, new = res(rtype, "x", **{prop: "one"}), res(rtype, "x", **{prop: "two"})
    c = one(old, new)
    assert c.significance == sig
    assert set(c.property_changes) == {prop}
    if prop not in ("maintenanceMode", "accessible", "cluster"):
        assert c.summary == f"{prop} one -> two"


def test_host_power_state_is_high_in_both_directions():
    c = one(res("host", "h", powerState="poweredOn"), res("host", "h", powerState="standBy"))
    assert c.significance == "high" and c.summary == "powerState poweredOn -> standBy"
    c = one(res("host", "h", powerState="standBy"), res("host", "h", powerState="poweredOn"))
    assert c.significance == "high"


@pytest.mark.parametrize(
    "rtype", ["host", "vm", "cluster", "datastore", "network", "vcenter", "datacenter", "folder"]
)
def test_rename_is_medium_on_every_type(rtype):
    old = Resource(id=f"{rtype}:vc01:1", type=rtype, name="before", source=SRC)
    new = Resource(id=f"{rtype}:vc01:1", type=rtype, name="after", source=SRC)
    c = one(old, new)
    assert c.significance == "medium"
    assert c.property_changes["name"].old == "before"
    assert c.property_changes["name"].new == "after"
    assert c.summary == "renamed before -> after"


def test_untracked_properties_never_produce_changes():
    old = res("host", "h", uptimeSeconds=1, cpuMhz=1, bootTime="x", overallStatus="green")
    new = res("host", "h", uptimeSeconds=2, cpuMhz=2, bootTime="y", overallStatus="red")
    assert diff([old], [new]) == []
    assert diff([res("vm", "v", guestState="running")], [res("vm", "v", guestState="x")]) == []


def test_change_carries_highest_significance_and_all_properties():
    old = res("host", "h", model="A", version="7", connectionState="connected")
    new = res("host", "h", model="B", version="8", connectionState="disconnected")
    c = one(old, new)
    assert c.significance == "high"
    assert set(c.property_changes) == {"model", "version", "connectionState"}
    assert "model A -> B" in c.summary and "version 7 -> 8" in c.summary


# ------------------------------------------------------------ scalar lists


@pytest.mark.parametrize(
    ("rtype", "prop", "sig"),
    [
        ("host", "ntpServers", "medium"),
        ("host", "dnsServers", "medium"),
        ("host", "standardSwitches", "low"),
        ("vm", "networks", "medium"),
        ("vm", "datastores", "medium"),
        ("datastore", "hosts", "medium"),
        ("network", "hosts", "low"),
    ],
)
def test_scalar_list_added_removed_summary(rtype, prop, sig):
    old = res(rtype, "x", **{prop: ["a", "b"]})
    new = res(rtype, "x", **{prop: ["b", "c"]})
    c = one(old, new)
    assert c.significance == sig
    assert c.summary == f"{prop} added c; removed a"
    assert c.property_changes[prop].old == ["a", "b"]
    assert c.property_changes[prop].new == ["b", "c"]


def test_scalar_lists_are_order_insensitive():
    for prop in SCALAR_LISTS - {"hosts"}:
        old = res("host", "h", **{prop: ["10.0.0.1", "10.0.0.2"]})
        new = res("host", "h", **{prop: ["10.0.0.2", "10.0.0.1"]})
        assert diff([old], [new]) == [], prop


def test_ntp_ipv6_entries_are_not_shortened():
    c = one(res("host", "h", ntpServers=[]), res("host", "h", ntpServers=["fe80::1"]))
    assert c.summary == "ntpServers added fe80::1"


def test_cluster_membership_is_medium_and_merges_with_other_cluster_props():
    old = [res("cluster", "c", hosts=["host:vc01:a"], haEnabled=True, ruleCount=1)]
    new = [res("cluster", "c", hosts=["host:vc01:a", "host:vc01:b"], haEnabled=False, ruleCount=2)]
    changes = diff(old, new)
    assert len(changes) == 1
    c = changes[0]
    assert c.significance == "high"
    assert set(c.property_changes) == {"hosts", "haEnabled", "ruleCount"}
    assert "hosts added b" in c.summary and "haEnabled true -> false" in c.summary


def test_cluster_flags_are_high_and_automation_level_medium():
    for prop in ("drsEnabled", "haEnabled", "vsanEnabled"):
        c = one(res("cluster", "c", **{prop: True}), res("cluster", "c", **{prop: False}))
        assert c.significance == "high" and c.summary == f"{prop} true -> false"
    c = one(
        res("cluster", "c", drsAutomationLevel="manual"),
        res("cluster", "c", drsAutomationLevel="fullyAutomated"),
    )
    assert c.significance == "medium"
    c = one(res("cluster", "c", evcMode=None), res("cluster", "c", evcMode="intel-skylake"))
    assert c.significance == "medium" and c.summary == "evcMode none -> intel-skylake"


# ------------------------------------------------------------ lists of dicts


def test_vmkernel_adapter_field_change_is_high_with_item_summary():
    old = res(
        "host",
        "h",
        vmkernelAdapters=[
            {"device": "vmk0", "ip": "10.0.0.1", "mtu": 1500, "portgroup": "mgmt"},
            {"device": "vmk1", "ip": "10.0.1.1", "mtu": 1500, "portgroup": "vmotion"},
        ],
    )
    new = res(
        "host",
        "h",
        vmkernelAdapters=[
            {"device": "vmk1", "ip": "10.0.1.1", "mtu": 9000, "portgroup": "vmotion"},
            {"device": "vmk0", "ip": "10.0.0.1", "mtu": 1500, "portgroup": "mgmt"},
        ],
    )
    c = one(old, new)
    assert c.significance == "high"
    assert c.summary == "vmk1 mtu 1500 -> 9000"
    assert c.property_changes["vmkernelAdapters"].old == old.properties["vmkernelAdapters"]
    assert c.property_changes["vmkernelAdapters"].new == new.properties["vmkernelAdapters"]


def test_dict_list_reorder_alone_is_not_a_change():
    a = {"device": "vmk0", "ip": "10.0.0.1", "mtu": 1500}
    b = {"device": "vmk1", "ip": "10.0.1.1", "mtu": 1500}
    assert (
        diff(
            [res("host", "h", vmkernelAdapters=[a, b])], [res("host", "h", vmkernelAdapters=[b, a])]
        )
        == []
    )


def test_disk_added_and_removed_is_medium():
    d1 = {"label": "Hard disk 1", "capacityBytes": 10, "datastore": "ds1", "thin": True}
    d2 = {"label": "Hard disk 2", "capacityBytes": 20, "datastore": "ds1", "thin": True}
    d3 = {"label": "Hard disk 3", "capacityBytes": 30, "datastore": "ds2", "thin": False}
    c = one(res("vm", "v", disks=[d1, d2]), res("vm", "v", disks=[d1, d3]))
    assert c.significance == "medium"
    assert c.summary == "Hard disk 3 added; Hard disk 2 removed"


def test_disk_grown_reports_field_change():
    d1 = {"label": "Hard disk 1", "capacityBytes": 10, "datastore": "ds1", "thin": True}
    d1b = dict(d1, capacityBytes=40)
    c = one(res("vm", "v", disks=[d1]), res("vm", "v", disks=[d1b]))
    assert c.summary == "Hard disk 1 capacityBytes 10 -> 40"


def test_nic_network_change_summary():
    n1 = {"label": "Network adapter 1", "mac": "00:50:56:aa", "network": "mgmt", "connected": True}
    n2 = {
        "label": "Network adapter 2",
        "mac": "00:50:56:bb",
        "network": "app-net",
        "connected": True,
    }
    n2b = dict(n2, network="dmz")
    c = one(res("vm", "v", nics=[n1, n2]), res("vm", "v", nics=[n1, n2b]))
    assert c.significance == "medium"
    assert c.summary == "Network adapter 2 network app-net -> dmz"


def test_nic_disconnect_and_multiple_fields():
    n1 = {"label": "Network adapter 1", "mac": "aa", "network": "mgmt", "connected": True}
    n1b = {"label": "Network adapter 1", "mac": "bb", "network": "mgmt", "connected": False}
    c = one(res("vm", "v", nics=[n1]), res("vm", "v", nics=[n1b]))
    assert c.summary == "Network adapter 1 connected true -> false; Network adapter 1 mac aa -> bb"


def test_physical_nic_link_speed_is_low():
    p = {"device": "vmnic0", "mac": "aa", "linkSpeedMb": 10000}
    c = one(
        res("host", "h", physicalNics=[p]),
        res("host", "h", physicalNics=[dict(p, linkSpeedMb=1000)]),
    )
    assert c.significance == "low"
    assert c.summary == "vmnic0 linkSpeedMb 10000 -> 1000"


# ------------------------------------------------------------ freeSpace banding kept


def test_free_space_banding_still_applies():
    assert (
        diff(
            [res("datastore", "d", capacity=1000, freeSpace=500)],
            [res("datastore", "d", capacity=1000, freeSpace=480)],
        )
        == []
    )
    c = one(
        res("datastore", "d", capacity=1000, freeSpace=500),
        res("datastore", "d", capacity=1000, freeSpace=30),
    )
    assert c.significance == "high" and c.summary == "usage 50.0% -> 97.0%"
    assert set(c.property_changes) == {"freeSpace", "usagePct"}


def test_datastore_hosts_and_maintenance_and_multiple_host_access():
    c = one(res("datastore", "d", hosts=["esx01"]), res("datastore", "d", hosts=["esx01", "esx02"]))
    assert c.significance == "medium" and c.summary == "hosts added esx02"
    c = one(
        res("datastore", "d", maintenanceMode=False), res("datastore", "d", maintenanceMode=True)
    )
    assert c.significance == "medium" and c.summary == "entered maintenance mode"
    c = one(
        res("datastore", "d", multipleHostAccess=True),
        res("datastore", "d", multipleHostAccess=False),
    )
    assert c.significance == "low"


def test_network_switch_and_ports_and_vcenter_versions():
    c = one(res("network", "n", switch="dvs-a"), res("network", "n", switch="dvs-b"))
    assert c.significance == "medium"
    c = one(res("network", "n", numPorts=8), res("network", "n", numPorts=16))
    assert c.significance == "low" and c.summary == "numPorts 8 -> 16"
    c = one(
        res("vcenter", "vc", version="8.0.2", apiVersion="8.0.2.0"),
        res("vcenter", "vc", version="8.0.3", apiVersion="8.0.3.0"),
    )
    assert c.significance == "medium" and set(c.property_changes) == {"version", "apiVersion"}


# ------------------------------------------------------------ odd shapes never crash


def test_missing_versus_present_list_is_reported_as_added():
    c = one(res("host", "h"), res("host", "h", ntpServers=["10.0.0.1"]))
    assert c.summary == "ntpServers added 10.0.0.1"
    assert c.property_changes["ntpServers"].old is None


def test_none_versus_empty_list_is_not_a_change_in_summary_terms():
    # None -> [] differs as a value; the engine must not crash and must say so plainly.
    c = one(res("host", "h", ntpServers=None), res("host", "h", ntpServers=[]))
    assert c.summary == "ntpServers changed"


def test_scalar_where_a_list_was_expected():
    c = one(res("host", "h", dnsServers="10.0.0.1"), res("host", "h", dnsServers=["10.0.0.2"]))
    assert c.summary == "dnsServers added 10.0.0.2; removed 10.0.0.1"
    c = one(
        res("host", "h", vmkernelAdapters="garbage"),
        res("host", "h", vmkernelAdapters=[{"device": "vmk0", "mtu": 1500}]),
    )
    assert c.significance == "high"
    assert "vmk0 added" in c.summary and "garbage removed" in c.summary


def test_unhashable_and_mixed_type_list_items():
    old = res("vm", "v", networks=[{"weird": 1}, ["nested"], 3, None])
    new = res("vm", "v", networks=[["nested"], 3, None, {"weird": 2}])
    c = one(old, new)
    assert c.significance == "medium"
    assert "added" in c.summary and "removed" in c.summary
    assert diff([old], [res("vm", "v", networks=[None, 3, ["nested"], {"weird": 1}])]) == []


def test_dict_items_missing_the_key_field_and_non_dict_items():
    old = res("vm", "v", disks=[{"capacityBytes": 1}, "raw", 7])
    new = res("vm", "v", disks=[{"capacityBytes": 2}, "raw", 8])
    c = one(old, new)
    assert c.significance == "medium"
    assert "8 added" in c.summary and "7 removed" in c.summary
    assert c.property_changes["disks"].old == old.properties["disks"]


def test_dict_item_with_nested_values_compares_without_crashing():
    old = res("vm", "v", nics=[{"label": "n1", "meta": {"a": [1, 2]}}])
    new = res("vm", "v", nics=[{"label": "n1", "meta": {"a": [2, 1]}}])
    c = one(old, new)
    assert c.summary == 'n1 meta {"a": [1, 2]} -> {"a": [2, 1]}'


def test_bool_versus_string_scalars_and_none_scalars():
    c = one(res("vm", "v", template=None), res("vm", "v", template=True))
    assert c.significance == "low" and c.summary == "template none -> true"
    c = one(res("vm", "v", numCpu="2"), res("vm", "v", numCpu=4))
    assert c.summary == "numCpu 2 -> 4"


def test_host_cluster_with_unusual_property_shape_does_not_crash():
    old = res("host", "h", cluster={"name": "wld01"})
    new = res("host", "h", cluster=["wld01"])
    changes = diff([old], [new])
    assert changes == [] or changes[0].resource_id == "host:vc01:h"


# ------------------------------------------------------------ min_significance


def _graph_with_all_levels():
    old = [res("host", "h", connectionState="connected", version="7", model="A")]
    new = [res("host", "h", connectionState="connected", version="7", model="B"), res("vm", "new")]
    old_cluster = res("cluster", "c", haEnabled=True, ruleCount=1)
    new_cluster = res("cluster", "c", haEnabled=False, ruleCount=1)
    old_vm = res("vm", "v", powerState="poweredOn")
    new_vm = res("vm", "v", powerState="poweredOff")
    return old + [old_cluster, old_vm], new + [new_cluster, new_vm]


def test_min_significance_filters_output():
    old, new = _graph_with_all_levels()
    everything = diff(old, new)
    assert [c.significance for c in everything] == ["high", "medium", "low", "low"]
    assert [c.resource_id for c in diff(old, new, min_significance="medium")] == [
        "cluster:vc01:c",
        "vm:vc01:v",
    ]
    assert [c.resource_id for c in diff(old, new, min_significance="high")] == ["cluster:vc01:c"]
    assert diff(old, new, min_significance="low") == everything


def test_min_significance_rejects_unknown_values():
    with pytest.raises(ValueError):
        diff([], [], min_significance="urgent")  # type: ignore[arg-type]
