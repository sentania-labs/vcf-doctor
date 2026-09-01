"""Collector and client tests with fake pyVmomi objects. No vCenter needed."""

import socket
import ssl
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pyVmomi import vim

from app.collectors.base import Collector
from app.collectors.vsphere import (
    VSphereAuthError,
    VSphereCollector,
    VSphereError,
    VSphereTLSError,
    VSphereUnreachableError,
)
from app.collectors.vsphere.client import (
    VSphereSession,
    build_ssl_context,
    classify_exception,
    split_host_port,
    to_plain,
)
from app.collectors.vsphere.normalize import PROPERTY_SPECS, RawInventory, RawObject

SMART = "app.collectors.vsphere.client.pyvim_connect.SmartConnect"
DISC = "app.collectors.vsphere.client.pyvim_connect.Disconnect"


def test_constructor_and_interface():
    c = VSphereCollector("vc01.lab.local", "administrator@vsphere.local", "pw")
    assert isinstance(c, Collector)
    assert c.verify_tls is False
    assert c.id == "vcenter:vc01"
    assert "host" in c.resource_types and "vm" in c.resource_types


def test_split_host_port():
    assert split_host_port("vc01") == ("vc01", 443)
    assert split_host_port("vc01:8443") == ("vc01", 8443)
    assert split_host_port("[::1]:9443") == ("::1", 9443)
    assert split_host_port("[::1]") == ("::1", 443)


def test_ssl_context_modes():
    off = build_ssl_context(False)
    assert off.check_hostname is False and off.verify_mode == ssl.CERT_NONE
    on = build_ssl_context(True)
    assert on.check_hostname is True and on.verify_mode == ssl.CERT_REQUIRED


def test_to_plain_converts_morefs_lists_and_enums():
    host = SimpleNamespace(_moId="host-12", _wsdlName="HostSystem")
    ds = SimpleNamespace(_moId="datastore-15", _wsdlName="Datastore")
    assert to_plain(host) == "host-12"
    assert to_plain([host, ds]) == ["host-12", "datastore-15"]
    assert to_plain(vim.HostSystem.ConnectionState.connected) == "connected"
    assert type(to_plain(vim.HostSystem.ConnectionState.connected)) is str
    assert to_plain(274877906944) == 274877906944
    assert to_plain(True) is True
    assert to_plain(None) is None


def test_to_plain_datetime_is_iso():
    t = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    assert to_plain(t) == "2026-08-01T12:00:00+00:00"


def test_to_plain_flattens_host_network_objects():
    vnic0 = vim.host.VirtualNic(
        device="vmk0",
        portgroup="Management Network",
        spec=vim.host.VirtualNic.Specification(
            ip=vim.host.IpConfig(ipAddress="10.0.0.11"), mtu=1500
        ),
    )
    vnic1 = vim.host.VirtualNic(
        device="vmk1",
        portgroup="",
        spec=vim.host.VirtualNic.Specification(
            ip=vim.host.IpConfig(ipAddress="10.0.1.11"),
            mtu=9000,
            distributedVirtualPort=vim.dvs.PortConnection(
                switchUuid="x", portgroupKey="dvportgroup-20"
            ),
        ),
    )
    assert to_plain([vnic0, vnic1]) == [
        {
            "device": "vmk0",
            "ip": "10.0.0.11",
            "mtu": 1500,
            "portgroup": "Management Network",
            "portgroupKey": None,
        },
        {
            "device": "vmk1",
            "ip": "10.0.1.11",
            "mtu": 9000,
            "portgroup": None,
            "portgroupKey": "dvportgroup-20",
        },
    ]
    up = vim.host.PhysicalNic(
        device="vmnic0",
        mac="aa:bb",
        linkSpeed=vim.host.PhysicalNic.LinkSpeedDuplex(speedMb=25000, duplex=True),
    )
    down = vim.host.PhysicalNic(device="vmnic1", mac="aa:bc")
    assert to_plain([up, down]) == [
        {"device": "vmnic0", "mac": "aa:bb", "linkSpeedMb": 25000},
        {"device": "vmnic1", "mac": "aa:bc", "linkSpeedMb": None},
    ]
    assert to_plain([vim.host.VirtualSwitch(name="vSwitch0")]) == [{"name": "vSwitch0"}]


def test_to_plain_flattens_vm_devices_and_drops_the_rest():
    dev = vim.vm.device
    disk = dev.VirtualDisk(
        key=2000,
        deviceInfo=vim.Description(label="Hard disk 1", summary=""),
        capacityInKB=1048576,
        capacityInBytes=1073741824,
        backing=dev.VirtualDisk.FlatVer2BackingInfo(
            fileName="[ds] a.vmdk",
            datastore=vim.Datastore("datastore-15"),
            thinProvisioned=True,
            diskMode="persistent",
        ),
    )
    # capacityInBytes missing: fall back to KB * 1024
    disk_kb = dev.VirtualDisk(
        key=2001,
        deviceInfo=vim.Description(label="Hard disk 2", summary=""),
        capacityInKB=2048,
        backing=dev.VirtualDisk.FlatVer2BackingInfo(
            fileName="[ds] b.vmdk", datastore=vim.Datastore("datastore-15"), diskMode="persistent"
        ),
    )
    nic_dvs = dev.VirtualVmxnet3(
        key=4000,
        deviceInfo=vim.Description(label="Network adapter 1", summary=""),
        macAddress="00:50:56:aa:bb:cc",
        backing=dev.VirtualEthernetCard.DistributedVirtualPortBackingInfo(
            port=vim.dvs.PortConnection(switchUuid="x", portgroupKey="dvportgroup-20")
        ),
        connectable=dev.VirtualDevice.ConnectInfo(
            connected=True, startConnected=True, allowGuestControl=True
        ),
    )
    nic_std = dev.VirtualE1000(
        key=4001,
        deviceInfo=vim.Description(label="Network adapter 2", summary=""),
        macAddress="00:50:56:aa:bb:cd",
        backing=dev.VirtualEthernetCard.NetworkBackingInfo(
            deviceName="VM Network", network=vim.Network("network-30")
        ),
        connectable=dev.VirtualDevice.ConnectInfo(
            connected=False, startConnected=True, allowGuestControl=True
        ),
    )
    controller = dev.VirtualLsiLogicController(key=1000, busNumber=0)
    out = to_plain([controller, disk, disk_kb, nic_dvs, nic_std])
    assert out == [
        {
            "kind": "disk",
            "label": "Hard disk 1",
            "capacityBytes": 1073741824,
            "datastore": "datastore-15",
            "thin": True,
        },
        {
            "kind": "disk",
            "label": "Hard disk 2",
            "capacityBytes": 2097152,
            "datastore": "datastore-15",
            "thin": None,
        },
        {
            "kind": "nic",
            "label": "Network adapter 1",
            "mac": "00:50:56:aa:bb:cc",
            "network": None,
            "portgroupKey": "dvportgroup-20",
            "opaqueNetworkId": None,
            "connected": True,
        },
        {
            "kind": "nic",
            "label": "Network adapter 2",
            "mac": "00:50:56:aa:bb:cd",
            "network": "network-30",
            "portgroupKey": None,
            "opaqueNetworkId": None,
            "connected": False,
        },
    ]


def test_to_plain_flattens_snapshot_tree_recursively():
    t1 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    t2 = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    child = vim.vm.SnapshotTree(
        snapshot=vim.vm.Snapshot("snapshot-2"),
        vm=vim.VirtualMachine("vm-101"),
        name="after",
        createTime=t2,
        state="poweredOn",
        quiesced=False,
    )
    root = vim.vm.SnapshotTree(
        snapshot=vim.vm.Snapshot("snapshot-1"),
        vm=vim.VirtualMachine("vm-101"),
        name="before-patch",
        createTime=t1,
        state="poweredOn",
        quiesced=False,
        childSnapshotList=[child],
    )
    assert to_plain([root]) == [
        {
            "name": "before-patch",
            "createTime": "2026-08-01T12:00:00+00:00",
            "children": [
                {"name": "after", "createTime": "2026-08-20T12:00:00+00:00", "children": []}
            ],
        }
    ]


def test_to_plain_flattens_dvportgroup_vlan_specs():
    vmw = vim.dvs.VmwareDistributedVirtualSwitch
    trunk = vmw.VmwarePortConfigPolicy(
        vlan=vmw.TrunkVlanSpec(
            vlanId=[vim.NumericRange(start=100, end=110), vim.NumericRange(start=5, end=5)]
        )
    )
    assert to_plain(trunk) == {"vlan": {"kind": "trunk", "ranges": [[100, 110], [5, 5]]}}
    assert to_plain(vmw.VmwarePortConfigPolicy(vlan=vmw.VlanIdSpec(vlanId=42))) == {
        "vlan": {"kind": "id", "vlanId": 42}
    }
    assert to_plain(vmw.VmwarePortConfigPolicy(vlan=vmw.PvlanSpec(pvlanId=200))) == {
        "vlan": {"kind": "pvlan", "pvlanId": 200}
    }
    # a non-VMware port setting has no vlan at all
    assert to_plain(vim.dvs.DistributedVirtualPort.Setting()) == {"vlan": None}


def test_to_plain_flattens_datastore_cluster_and_opaque_summaries():
    mount = vim.Datastore.HostMount(
        key=vim.HostSystem("host-12"),
        mountInfo=vim.host.MountInfo(mounted=True, accessible=True),
    )
    assert to_plain([mount]) == [{"host": "host-12", "mounted": True, "accessible": True}]
    vmfs = vim.host.VmfsDatastoreInfo(
        name="ds",
        url="ds:///",
        vmfs=vim.host.VmfsVolume(
            version="6.82",
            name="ds",
            uuid="u",
            blockSizeMb=1,
            maxBlocks=1,
            majorVersion=6,
            capacity=1,
            extent=[vim.host.ScsiDisk.Partition(diskName="naa.1", partition=1)],
            vmfsUpgradable=False,
            ssd=True,
        ),
    )
    assert to_plain(vmfs) == {"vmfsVersion": "6.82"}
    assert to_plain(vim.host.NasDatastoreInfo(name="nfs", url="ds:///")) == {"vmfsVersion": None}
    summary = vim.ClusterComputeResource.Summary(
        currentEVCModeKey="intel-icelake",
        totalCpu=96000,
        totalMemory=2**40,
        numHosts=4,
        numCpuCores=1,
        numCpuThreads=1,
        effectiveCpu=1,
        effectiveMemory=1,
        numEffectiveHosts=1,
        overallStatus="green",
    )
    assert to_plain(summary) == {
        "currentEVCModeKey": "intel-icelake",
        "totalCpu": 96000,
        "totalMemory": 2**40,
        "numHosts": 4,
    }
    opaque = vim.OpaqueNetwork.Summary(
        opaqueNetworkId="seg-1",
        opaqueNetworkType="nsx.LogicalSwitch",
        name="nsx-seg",
        accessible=True,
    )
    assert to_plain(opaque) == {
        "opaqueNetworkType": "nsx.LogicalSwitch",
        "opaqueNetworkId": "seg-1",
    }
    assert to_plain([vim.cluster.AffinityRuleSpec(name="r1", enabled=True, key=1)]) == [
        {"name": "r1", "enabled": True}
    ]


@pytest.mark.parametrize(
    ("exc", "cls", "needle"),
    [
        (vim.fault.InvalidLogin(), VSphereAuthError, "username or password"),
        (ssl.SSLCertVerificationError(1, "certificate verify failed"), VSphereTLSError, "TLS"),
        (ssl.SSLError(1, "handshake"), VSphereTLSError, "TLS"),
        (socket.gaierror(-2, "Name or service not known"), VSphereUnreachableError, "resolve"),
        (ConnectionRefusedError(111, "refused"), VSphereUnreachableError, "refused"),
        (TimeoutError(), VSphereUnreachableError, "timed out"),
        (OSError(113, "No route to host"), VSphereUnreachableError, "No route"),
        (RuntimeError("boom"), VSphereError, "boom"),
    ],
)
def test_classify_exception(exc, cls, needle):
    err = classify_exception(exc, "vc01")
    assert isinstance(err, cls)
    assert needle.lower() in str(err).lower()
    assert "vc01" in str(err)


def _fake_si(version="8.0.3", build="24022515"):
    about = SimpleNamespace(
        name="VMware vCenter Server",
        fullName=f"VMware vCenter Server {version} build-{build}",
        version=version,
        build=build,
        instanceUuid="6d3f0a5e-0000-4000-8000-000000000001",
        apiType="VirtualCenter",
    )
    si = MagicMock()
    si.RetrieveContent.return_value = SimpleNamespace(about=about)
    return si


def test_test_connection_ok_and_disconnects():
    si = _fake_si()
    with patch(SMART, return_value=si) as smart, patch(DISC) as disc:
        r = VSphereCollector("vc01.lab.local", "u", "p").test_connection()
    assert r.ok is True
    assert r.version == "8.0.3"
    assert r.build == "24022515"
    assert "vCenter" in r.message
    smart.assert_called_once()
    kwargs = smart.call_args.kwargs
    assert kwargs["host"] == "vc01.lab.local" and kwargs["port"] == 443
    assert kwargs["sslContext"].verify_mode == ssl.CERT_NONE
    disc.assert_called_once_with(si)


def test_test_connection_uses_verified_context_when_asked():
    with patch(SMART, return_value=_fake_si()) as smart, patch(DISC):
        VSphereCollector("vc01", "u", "p", verify_tls=True).test_connection()
    assert smart.call_args.kwargs["sslContext"].verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize(
    ("exc", "needle"),
    [
        (vim.fault.InvalidLogin(), "username or password"),
        (socket.gaierror(-2, "unknown"), "resolve"),
        (ssl.SSLCertVerificationError(1, "self signed certificate"), "TLS"),
        (TimeoutError(), "timed out"),
        (RuntimeError("weird"), "weird"),
    ],
)
def test_test_connection_failures_do_not_raise(exc, needle):
    with patch(SMART, side_effect=exc), patch(DISC) as disc:
        r = VSphereCollector("vc01", "u", "p").test_connection()
    assert r.ok is False
    assert needle.lower() in r.message.lower()
    assert r.version is None and r.build is None
    disc.assert_not_called()


def test_collect_raises_vsphere_error_on_bad_credentials():
    with patch(SMART, side_effect=vim.fault.InvalidLogin()), patch(DISC):
        with pytest.raises(VSphereError) as ei:
            VSphereCollector("vc01", "u", "p").collect()
    assert isinstance(ei.value, VSphereAuthError)


def test_collect_raises_vsphere_error_when_unreachable():
    with patch(SMART, side_effect=ConnectionRefusedError(111, "refused")), patch(DISC):
        with pytest.raises(VSphereUnreachableError):
            VSphereCollector("vc01", "u", "p").collect()


def test_collect_normalizes_inventory_and_disconnects():
    inv = RawInventory(
        host="vc01.lab.local",
        name="vc01",
        version="8.0.3",
        build="1",
        instance_uuid="x",
        objects=[
            RawObject("datacenter-2", "Datacenter", {"name": "DC1", "parent": None}),
            RawObject(
                "host-12",
                "HostSystem",
                {"name": "esx01", "parent": "datacenter-2", "runtime.connectionState": "connected"},
            ),
        ],
    )
    si = _fake_si()
    with (
        patch(SMART, return_value=si),
        patch(DISC) as disc,
        patch.object(VSphereSession, "inventory", return_value=inv),
    ):
        resources = VSphereCollector("vc01.lab.local", "u", "p").collect()
    ids = {r.id for r in resources}
    assert ids == {"vcenter:vc01", "datacenter:vc01:datacenter-2", "host:vc01:host-12"}
    disc.assert_called_once_with(si)


def test_session_retrieve_merges_base_and_subclass_views():
    """Fake PropertyCollector: the ComputeResource view returns a cluster too;
    the merged inventory must keep one object with the specific kind."""

    def make_oc(moid, wsdl, props):
        return SimpleNamespace(
            obj=SimpleNamespace(_moId=moid, _wsdlName=wsdl),
            propSet=[SimpleNamespace(name=k, val=v) for k, v in props.items()],
        )

    per_type = {
        vim.ComputeResource: [make_oc("domain-c7", "ClusterComputeResource", {"name": "cl"})],
        vim.ClusterComputeResource: [
            make_oc(
                "domain-c7",
                "ClusterComputeResource",
                {"configuration.drsConfig.enabled": True},
            )
        ],
        vim.Network: [make_oc("dvportgroup-1", "DistributedVirtualPortgroup", {"name": "pg"})],
    }
    current: dict[str, type] = {}

    def create_view(root, types, recursive):
        current["type"] = types[0]
        return vim.view.ContainerView("view-1")

    def retrieve_ex(specs, opts):
        return SimpleNamespace(objects=per_type.get(current["type"], []), token=None)

    content = MagicMock()
    content.about = SimpleNamespace(
        name="vc", fullName="vc", version="8", build="1", instanceUuid="u", apiType="VirtualCenter"
    )
    content.viewManager.CreateContainerView.side_effect = create_view
    content.propertyCollector.RetrievePropertiesEx.side_effect = retrieve_ex
    si = MagicMock()
    si.RetrieveContent.return_value = content

    with patch(SMART, return_value=si), patch(DISC):
        with VSphereSession("vc01", "u", "p") as s:
            inv = s.inventory()
    by = {o.moref: o for o in inv.objects}
    assert by["domain-c7"].kind == "ClusterComputeResource"
    assert by["domain-c7"].props == {
        "name": "cl",
        "configuration.drsConfig.enabled": True,
    }
    assert by["dvportgroup-1"].kind == "DistributedVirtualPortgroup"
    # one container view per property spec type, each destroyed
    assert content.viewManager.CreateContainerView.call_count == len(PROPERTY_SPECS)
    assert content.viewManager.CreateContainerView.call_count == 12
