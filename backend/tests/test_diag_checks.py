"""Diagnostic checks against synthetic normalized resource graphs."""

from app.diagnostics.registry import list_checks, run_all
from app.models import Relationship, Resource

SRC = "vcenter:vc01"


def cluster(name: str, **props) -> Resource:
    return Resource(
        id=f"cluster:vc01:{name}", type="cluster", name=name, source=SRC, properties=props
    )


def host(name: str, cl: str = "wld01", **props) -> Resource:
    base = {"connectionState": "connected", "powerState": "poweredOn", "maintenanceMode": False}
    base.update(props)
    base.setdefault("cluster", f"cluster:vc01:{cl}")
    return Resource(
        id=f"host:vc01:{name}",
        type="host",
        name=name,
        source=SRC,
        parent_id=f"cluster:vc01:{cl}",
        properties=base,
    )


def vm(name: str, h: str = "esx01", **props) -> Resource:
    base = {"powerState": "poweredOn", "host": f"host:vc01:{h}", "networks": [], "datastores": []}
    base.update(props)
    return Resource(id=f"vm:vc01:{name}", type="vm", name=name, source=SRC, properties=base)


def datastore(name: str, capacity: int = 1000, free: int = 500, **props) -> Resource:
    base = {"capacity": capacity, "freeSpace": free, "accessible": True}
    base.update(props)
    return Resource(id=f"ds:vc01:{name}", type="datastore", name=name, source=SRC, properties=base)


def network(name: str) -> Resource:
    return Resource(id=f"net:vc01:{name}", type="network", name=name, source=SRC)


def healthy() -> list[Resource]:
    return [
        cluster("wld01"),
        host("esx01"),
        host("esx02"),
        vm("web01", "esx01", networks=["net:vc01:vlan10"], datastores=["ds:vc01:vsan"]),
        datastore("vsan"),
        network("vlan10"),
    ]


def ids(findings, check_id):
    return sorted(f.resource_id for f in findings if f.check_id == check_id)


def one(findings, check_id):
    hits = [f for f in findings if f.check_id == check_id]
    assert len(hits) == 1, f"expected one {check_id}, got {hits}"
    return hits[0]


# ------------------------------------------------------------------ registry


def test_list_checks_has_all_required_ids():
    got = {c["id"] for c in list_checks()}
    assert got == {
        "HOST_DISCONNECTED",
        "HOST_NOT_RESPONDING",
        "HOST_MAINTENANCE_MODE",
        "DATASTORE_HIGH_USAGE",
        "DATASTORE_INACCESSIBLE",
        "VM_POWERED_OFF",
        "VM_ORPHANED_OR_INACCESSIBLE",
        "CLUSTER_HOST_COUNT_CHANGE",
        "NETWORK_REMOVED",
        "RESOURCE_REMOVED",
        "HOST_COUNT_LOW",
    }
    for c in list_checks():
        assert c["name"] and c["description"]


def test_healthy_graph_no_findings_without_previous():
    assert run_all(healthy()) == []


def test_healthy_graph_no_findings_with_identical_previous():
    assert run_all(healthy(), healthy()) == []


def test_findings_are_deterministic_and_fully_populated():
    res = healthy() + [host("esx03", connectionState="disconnected")]
    a = run_all(res)
    b = run_all(res)
    assert [f.id for f in a] == [f.id for f in b]
    f = one(a, "HOST_DISCONNECTED")
    assert f.id == "HOST_DISCONNECTED:host:vc01:esx03"
    assert f.resource_name == "esx03"
    assert f.resource_type == "host"
    assert f.recommendation
    assert f.title and f.summary


def test_run_all_sorts_critical_first():
    res = healthy() + [
        vm("old01", powerState="poweredOff"),
        host("esx09", maintenanceMode=True),
        datastore("nfs01", accessible=False),
    ]
    sev = [f.severity for f in run_all(res)]
    assert sev == sorted(sev, key={"critical": 0, "warning": 1, "info": 2}.get)
    assert sev[0] == "critical"


# ------------------------------------------------------------------ hosts


def test_host_disconnected():
    f = one(
        run_all(healthy() + [host("esx03", connectionState="disconnected")]), "HOST_DISCONNECTED"
    )
    assert f.severity == "critical"
    assert f.evidence["connectionState"] == "disconnected"


def test_host_not_responding_is_separate_from_disconnected():
    findings = run_all(healthy() + [host("esx03", connectionState="notResponding")])
    f = one(findings, "HOST_NOT_RESPONDING")
    assert f.severity == "critical"
    assert ids(findings, "HOST_DISCONNECTED") == []


def test_host_maintenance_mode():
    f = one(run_all(healthy() + [host("esx03", maintenanceMode=True)]), "HOST_MAINTENANCE_MODE")
    assert f.severity == "warning"
    assert f.evidence["maintenanceMode"] is True


# ------------------------------------------------------------------ datastores


def test_datastore_usage_thresholds():
    res = [
        datastore("ok", 1000, 200),  # 80%
        datastore("warn", 1000, 150),  # 85% exactly
        datastore("warn2", 1000, 60),  # 94%
        datastore("crit", 1000, 50),  # 95% exactly
        datastore("crit2", 1000, 0),  # 100%
        datastore("nocap", capacity=0, free=0),
    ]
    findings = run_all(res)
    by_ds = {f.resource_name: f for f in findings if f.check_id == "DATASTORE_HIGH_USAGE"}
    assert set(by_ds) == {"warn", "warn2", "crit", "crit2"}
    assert by_ds["warn"].severity == "warning"
    assert by_ds["warn2"].severity == "warning"
    assert by_ds["crit"].severity == "critical"
    assert by_ds["crit2"].severity == "critical"
    ev = by_ds["crit"].evidence
    assert ev["pct"] == 95.0 and ev["capacity"] == 1000 and ev["freeSpace"] == 50


def test_datastore_inaccessible():
    f = one(run_all([datastore("nfs01", accessible=False)]), "DATASTORE_INACCESSIBLE")
    assert f.severity == "critical"
    assert f.evidence["accessible"] is False


# ------------------------------------------------------------------ vms


def test_vm_powered_off_skips_templates():
    res = [
        vm("off01", powerState="poweredOff"),
        vm("tmpl01", powerState="poweredOff", template=True),
    ]
    findings = run_all(res)
    assert ids(findings, "VM_POWERED_OFF") == ["vm:vc01:off01"]
    assert one(findings, "VM_POWERED_OFF").severity == "info"


def test_vm_orphaned_or_inaccessible_from_connection_state_and_flags():
    res = [
        vm("orph", connectionState="orphaned"),
        vm("inacc", connectionState="inaccessible"),
        vm("flag", inaccessible=True, overallStatus="red"),
        vm("fine", connectionState="connected", overallStatus="green"),
    ]
    findings = run_all(res)
    assert ids(findings, "VM_ORPHANED_OR_INACCESSIBLE") == [
        "vm:vc01:flag",
        "vm:vc01:inacc",
        "vm:vc01:orph",
    ]
    assert all(
        f.severity == "critical" for f in findings if f.check_id == "VM_ORPHANED_OR_INACCESSIBLE"
    )


# ------------------------------------------------------------------ clusters


def test_cluster_host_count_change_requires_previous():
    prev = healthy() + [host("esx03")]
    cur = healthy()
    assert ids(run_all(cur), "CLUSTER_HOST_COUNT_CHANGE") == []
    f = one(run_all(cur, prev), "CLUSTER_HOST_COUNT_CHANGE")
    assert f.severity == "warning"
    assert f.evidence["previousHostCount"] == 3
    assert f.evidence["currentHostCount"] == 2
    assert f.evidence["hostsRemoved"] == ["host:vc01:esx03"]


def test_cluster_host_count_change_uses_hosts_property_when_present():
    prev = [cluster("mgmt", hosts=["host:vc01:a", "host:vc01:b"])]
    cur = [cluster("mgmt", hosts=["host:vc01:a"])]
    f = one(run_all(cur, prev), "CLUSTER_HOST_COUNT_CHANGE")
    assert f.evidence["hostsRemoved"] == ["host:vc01:b"]


def test_host_count_low():
    res = [
        cluster("wld01"),
        host("esx01"),
        cluster("wld02"),
        cluster("wld03"),
        host("a", "wld03"),
        host("b", "wld03"),
    ]
    findings = run_all(res)
    assert ids(findings, "HOST_COUNT_LOW") == ["cluster:vc01:wld01", "cluster:vc01:wld02"]
    f = [x for x in findings if x.resource_id == "cluster:vc01:wld01"][0]
    assert f.severity == "warning" and f.evidence["hostCount"] == 1


def test_host_cluster_derived_from_relationship_or_parent():
    h_rel = Resource(
        id="host:vc01:r1",
        type="host",
        name="r1",
        source=SRC,
        relationships=[Relationship(kind="member_of", target_id="cluster:vc01:c1")],
    )
    h_parent = Resource(
        id="host:vc01:p1", type="host", name="p1", source=SRC, parent_id="cluster:vc01:c1"
    )
    assert ids(run_all([cluster("c1"), h_rel, h_parent]), "HOST_COUNT_LOW") == []


# ------------------------------------------------------------------ removals


def test_network_removed_needs_previous_and_lists_affected_vms():
    prev = healthy()
    cur = [r for r in healthy() if r.type != "network"]
    assert ids(run_all(cur), "NETWORK_REMOVED") == []
    findings = run_all(cur, prev)
    f = one(findings, "NETWORK_REMOVED")
    assert f.severity == "critical"
    assert f.resource_type == "network"
    assert f.evidence["affectedVms"] == ["web01"]
    # Networks are not double reported by RESOURCE_REMOVED.
    assert ids(findings, "RESOURCE_REMOVED") == []


def test_resource_removed_covers_other_types():
    prev = healthy() + [datastore("nfs01"), vm("gone01")]
    cur = healthy()
    assert ids(run_all(cur), "RESOURCE_REMOVED") == []
    findings = run_all(cur, prev)
    assert ids(findings, "RESOURCE_REMOVED") == ["ds:vc01:nfs01", "vm:vc01:gone01"]
    for f in findings:
        if f.check_id == "RESOURCE_REMOVED":
            assert f.severity == "warning"
            assert f.resource_name and f.resource_type
            assert f.id == f"RESOURCE_REMOVED:{f.resource_id}"


def test_cluster_membership_resolves_cluster_name_property():
    """Collectors put the cluster NAME in properties.cluster; membership must
    still resolve to the cluster id (regression for a false HOST_COUNT_LOW)."""
    from app.diagnostics.checks._common import cluster_members
    from app.models import Resource

    cluster = Resource(id="cluster:vc:c1", type="cluster", name="wld01-cl01", source="vcenter:vc")
    hosts = [
        Resource(
            id=f"host:vc:h{i}",
            type="host",
            name=f"esx0{i}",
            source="vcenter:vc",
            properties={"cluster": "wld01-cl01"},
        )
        for i in range(1, 4)
    ]
    members = cluster_members([cluster, *hosts])
    assert members["cluster:vc:c1"] == {"host:vc:h1", "host:vc:h2", "host:vc:h3"}
