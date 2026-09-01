"""Deep-inventory diagnostic checks: snapshots, Tools, NTP, HA/DRS, version skew.

Every check must tolerate the properties being absent (older snapshots and
fixtures without them): no finding, never an exception."""

from datetime import UTC, datetime, timedelta

from app.diagnostics.checks.cluster import ClusterDrsDisabled, ClusterHaDisabled
from app.diagnostics.checks.host import HostNtpNotConfigured, HostVersionMismatch
from app.diagnostics.checks.vm import VmSnapshotStale, VmToolsNotRunning
from app.diagnostics.registry import run_all
from app.models import Resource

SRC = "vcenter:vc01"


def cluster(name: str, **props) -> Resource:
    return Resource(
        id=f"cluster:vc01:{name}", type="cluster", name=name, source=SRC, properties=props
    )


def host(name: str, cl: str = "wld01", **props) -> Resource:
    base = {"connectionState": "connected", "powerState": "poweredOn", "cluster": cl}
    base.update(props)
    return Resource(
        id=f"host:vc01:{name}",
        type="host",
        name=name,
        source=SRC,
        parent_id=f"cluster:vc01:{cl}",
        properties=base,
    )


def vm(name: str, **props) -> Resource:
    base = {"powerState": "poweredOn", "host": "esx01"}
    base.update(props)
    return Resource(id=f"vm:vc01:{name}", type="vm", name=name, source=SRC, properties=base)


def days_ago(n: float) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).isoformat()


def by_check(findings, check_id):
    return [f for f in findings if f.check_id == check_id]


def assert_populated(f):
    assert f.id == f"{f.check_id}:{f.resource_id}"
    assert f.title and f.summary and f.recommendation
    assert isinstance(f.evidence, dict) and f.evidence


# ------------------------------------------------------------ absence tolerance


def test_no_findings_when_deep_properties_are_absent():
    graph = [cluster("wld01"), host("esx01"), host("esx02"), vm("web01")]
    findings = run_all(graph)
    for cid in (
        "VM_SNAPSHOT_STALE",
        "VM_TOOLS_NOT_RUNNING",
        "HOST_NTP_NOT_CONFIGURED",
        "CLUSTER_HA_DISABLED",
        "CLUSTER_DRS_DISABLED",
        "HOST_VERSION_MISMATCH",
    ):
        assert by_check(findings, cid) == [], cid


def test_null_deep_properties_are_treated_as_unknown():
    graph = [
        cluster("wld01", haEnabled=None, drsEnabled=None),
        host("esx01", ntpServers=None, version=None, build=None),
        host("esx02", ntpServers=None, version=None, build=None),
        vm("web01", toolsStatus=None, snapshotCount=None, oldestSnapshotTime=None),
    ]
    findings = run_all(graph)
    assert not any(
        f.check_id
        in {
            "VM_SNAPSHOT_STALE",
            "VM_TOOLS_NOT_RUNNING",
            "HOST_NTP_NOT_CONFIGURED",
            "CLUSTER_HA_DISABLED",
            "CLUSTER_DRS_DISABLED",
            "HOST_VERSION_MISMATCH",
        }
        for f in findings
    )


# ------------------------------------------------------------ VM_SNAPSHOT_STALE


def test_snapshot_stale_by_age():
    out = VmSnapshotStale().evaluate([vm("a", snapshotCount=1, oldestSnapshotTime=days_ago(9))])
    assert len(out) == 1
    f = out[0]
    assert_populated(f)
    assert f.severity == "warning"
    assert f.id == "VM_SNAPSHOT_STALE:vm:vc01:a"
    assert f.evidence["oldestSnapshotAgeDays"] == 9
    assert f.evidence["snapshotCount"] == 1
    assert "9 days old" in f.summary
    assert "Snapshot Manager" in f.recommendation


def test_snapshot_stale_by_count():
    out = VmSnapshotStale().evaluate([vm("a", snapshotCount=4, oldestSnapshotTime=days_ago(1))])
    assert len(out) == 1 and "4 snapshots" in out[0].summary
    # Count alone, no timestamp at all.
    assert len(VmSnapshotStale().evaluate([vm("b", snapshotCount=5)])) == 1


def test_snapshot_thresholds_are_strict():
    fresh = [
        vm("a", snapshotCount=3, oldestSnapshotTime=days_ago(6.9)),
        vm("b", snapshotCount=0, oldestSnapshotTime=None),
        vm("c", snapshotCount=3, oldestSnapshotTime=days_ago(7)),  # exactly 7 days is not stale
    ]
    assert VmSnapshotStale().evaluate(fresh) == []


def test_snapshot_accepts_z_suffix_and_naive_timestamps_and_junk():
    z = (datetime.now(UTC) - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    naive = (datetime.now(UTC) - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%S")
    out = VmSnapshotStale().evaluate(
        [
            vm("z", snapshotCount=1, oldestSnapshotTime=z),
            vm("n", snapshotCount=1, oldestSnapshotTime=naive),
            vm("junk", snapshotCount=1, oldestSnapshotTime="last tuesday"),
            vm("num", snapshotCount="2", oldestSnapshotTime=12345),
        ]
    )
    assert sorted(f.resource_name for f in out) == ["n", "z"]


# ------------------------------------------------------------ VM_TOOLS_NOT_RUNNING


def test_tools_not_running_and_not_installed():
    out = VmToolsNotRunning().evaluate(
        [
            vm("a", toolsStatus="toolsNotRunning"),
            vm("b", toolsStatus="toolsNotInstalled"),
            vm("ok", toolsStatus="toolsOk"),
            vm("old", toolsStatus="toolsOld"),
        ]
    )
    assert sorted(f.resource_name for f in out) == ["a", "b"]
    for f in out:
        assert_populated(f)
        assert f.severity == "info"
    by_name = {f.resource_name: f for f in out}
    assert "Install VMware Tools" in by_name["b"].recommendation
    assert "Start the VMware Tools service" in by_name["a"].recommendation
    assert by_name["a"].evidence["toolsStatus"] == "toolsNotRunning"


def test_tools_check_skips_powered_off_and_templates():
    out = VmToolsNotRunning().evaluate(
        [
            vm("off", powerState="poweredOff", toolsStatus="toolsNotRunning"),
            vm("tpl", template=True, toolsStatus="toolsNotInstalled"),
            vm("tpl2", isTemplate=True, toolsStatus="toolsNotInstalled"),
        ]
    )
    assert out == []


# ------------------------------------------------------------ HOST_NTP_NOT_CONFIGURED


def test_ntp_empty_list_is_a_warning_but_absent_is_not():
    out = HostNtpNotConfigured().evaluate(
        [
            host("empty", ntpServers=[]),
            host("ok", ntpServers=["10.0.0.1"]),
            host("absent"),
            host("null", ntpServers=None),
            host("weird", ntpServers="10.0.0.1"),
        ]
    )
    assert [f.resource_name for f in out] == ["empty"]
    f = out[0]
    assert_populated(f)
    assert f.severity == "warning"
    assert f.id == "HOST_NTP_NOT_CONFIGURED:host:vc01:empty"
    assert f.evidence["ntpServers"] == []
    assert "Time Configuration" in f.recommendation


# ------------------------------------------------------------ CLUSTER_HA / DRS


def test_ha_disabled_is_warning_and_drs_disabled_is_info():
    graph = [
        cluster("a", haEnabled=False, drsEnabled=False, numVms=12),
        cluster("b", haEnabled=True, drsEnabled=True),
        cluster("c"),
    ]
    ha = ClusterHaDisabled().evaluate(graph)
    drs = ClusterDrsDisabled().evaluate(graph)
    assert [f.resource_name for f in ha] == ["a"]
    assert [f.resource_name for f in drs] == ["a"]
    assert_populated(ha[0])
    assert_populated(drs[0])
    assert ha[0].severity == "warning" and drs[0].severity == "info"
    assert ha[0].id == "CLUSTER_HA_DISABLED:cluster:vc01:a"
    assert drs[0].id == "CLUSTER_DRS_DISABLED:cluster:vc01:a"
    assert "12 VMs" in ha[0].summary
    assert ha[0].evidence["haEnabled"] is False
    assert "Enable vSphere HA" in ha[0].recommendation
    assert "Enable DRS" in drs[0].recommendation


def test_ha_drs_string_values_do_not_trigger():
    # Only a real False counts; "false" strings or 0 are left alone rather than guessed.
    graph = [cluster("a", haEnabled="false", drsEnabled=0)]
    assert ClusterHaDisabled().evaluate(graph) == []
    assert ClusterDrsDisabled().evaluate(graph) == []


# ------------------------------------------------------------ HOST_VERSION_MISMATCH


def test_version_mismatch_reports_once_per_cluster():
    graph = [
        cluster("wld01"),
        cluster("wld02"),
        host("esx01", "wld01", version="8.0.2", build="22380479"),
        host("esx02", "wld01", version="8.0.2", build="22380479"),
        host("esx03", "wld01", version="8.0.3", build="24022510"),
        host("esx04", "wld02", version="8.0.2", build="22380479"),
        host("esx05", "wld02", version="8.0.2", build="22380479"),
    ]
    out = HostVersionMismatch().evaluate(graph)
    assert len(out) == 1
    f = out[0]
    assert_populated(f)
    assert f.severity == "warning"
    assert f.resource_id == "cluster:vc01:wld01"
    assert f.id == "HOST_VERSION_MISMATCH:cluster:vc01:wld01"
    assert f.evidence["hosts"] == {
        "esx01": "8.0.2 build 22380479",
        "esx02": "8.0.2 build 22380479",
        "esx03": "8.0.3 build 24022510",
    }
    assert len(f.evidence["versions"]) == 2
    assert "Lifecycle Manager" in f.recommendation


def test_version_mismatch_same_version_different_build_counts():
    graph = [
        cluster("wld01"),
        host("esx01", version="8.0.2", build="1"),
        host("esx02", version="8.0.2", build="2"),
    ]
    assert len(HostVersionMismatch().evaluate(graph)) == 1


def test_version_mismatch_ignores_hosts_without_version_and_single_host_clusters():
    graph = [
        cluster("wld01"),
        host("esx01", version="8.0.2", build="1"),
        host("esx02"),  # older collector: nothing reported
        cluster("solo"),
        host("esx09", "solo", version="7.0.3", build="9"),
    ]
    assert HostVersionMismatch().evaluate(graph) == []


def test_version_mismatch_uses_cluster_hosts_property_when_hosts_lack_parent():
    graph = [
        cluster("wld01", hosts=["host:vc01:a", "host:vc01:b"]),
        Resource(
            id="host:vc01:a",
            type="host",
            name="a",
            source=SRC,
            properties={"version": "8.0.2", "build": "1"},
        ),
        Resource(
            id="host:vc01:b",
            type="host",
            name="b",
            source=SRC,
            properties={"version": "8.0.3", "build": "2"},
        ),
    ]
    out = HostVersionMismatch().evaluate(graph)
    assert [f.resource_id for f in out] == ["cluster:vc01:wld01"]


# ------------------------------------------------------------ registry integration


def test_run_all_orders_new_findings_by_severity_and_is_deterministic():
    graph = [
        cluster("wld01", haEnabled=False, drsEnabled=False),
        host("esx01", ntpServers=[], version="8.0.2", build="1"),
        host("esx02", ntpServers=["10.0.0.1"], version="8.0.3", build="2"),
        vm("a", toolsStatus="toolsNotRunning", snapshotCount=9),
    ]
    first = run_all(graph)
    second = run_all(graph)
    assert [f.id for f in first] == [f.id for f in second]
    got = {f.check_id for f in first}
    assert {
        "CLUSTER_HA_DISABLED",
        "CLUSTER_DRS_DISABLED",
        "HOST_NTP_NOT_CONFIGURED",
        "HOST_VERSION_MISMATCH",
        "VM_TOOLS_NOT_RUNNING",
        "VM_SNAPSHOT_STALE",
    } <= got
    rank = {"critical": 0, "warning": 1, "info": 2}
    assert [rank[f.severity] for f in first] == sorted(rank[f.severity] for f in first)
