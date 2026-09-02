"""Deterministic fixture generator for the VCF Doctor test fixtures.

Produces fixtures/snapshot_a.json (healthy baseline) and
fixtures/snapshot_b.json (degraded) describing one fictional VCF workload
domain. Run from anywhere:

    python fixtures/generate.py

Every value is hard coded or derived from a fixed table, so re-running the
script yields byte-identical output. Tweak the tables, re-run, and commit.

Every resource carries every property listed for its type in
docs/PROPERTIES.md (the contract). Timestamps are derived from BASE_TIME, a
fixed instant, never from the wall clock.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent

VC = "vc-wld01"
SOURCE = f"vcenter:{VC}"
DOMAIN = "wld01.vcf.example"
DATACENTER = "wld01-dc"
VDS = "wld01-vds01"

TIB = 1024**4
GIB = 1024**3
MIB = 1024**2

# Fixed instant the snapshots pretend to have been collected at. Host and VM
# boot times and snapshot ages are all offsets from this.
BASE_TIME = datetime(2026, 8, 31, 6, 0, 0, tzinfo=UTC)

# Change knobs for snapshot B. Kept at the top so they are easy to find.
DISCONNECTED_HOST = "esx03"
MAINTENANCE_HOST = "esx07"
MIGRATED_VM = "app02"
MIGRATED_FROM = "esx02"
MIGRATED_TO = "esx04"
POWERED_OFF_VM = "web03"
FULL_DATASTORE = "wld01-cl01-vsan01"
FULL_DATASTORE_USED_PCT = 0.91
REMOVED_NETWORK = "seg-dmz-10.20.40.0"
MTU_HOST = "esx02"
MTU_VMK = "vmk1"
MTU_NEW = 1500
VLAN_NETWORK = "pg-vmotion"
VLAN_NEW = 201
NTP_HOST = "esx04"
NTP_DROPPED = f"ntp2.{DOMAIN}"
DISK_VM = "app01"
DISK_NEW = {"label": "Hard disk 3", "capacityBytes": 100 * GIB, "thin": True}
MEMORY_VM = "db01"
MEMORY_NEW_MB = 49152
RENAMED_VM = "web02"
RENAMED_TO = "web02-old"
DRS_CLUSTER = "wld01-edge"
DRS_NEW_LEVEL = "manual"

# Snapshot outliers in A (unchanged in B, so VM_SNAPSHOT_STALE fires in both).
MANY_SNAPSHOTS_VM = "backup-proxy01"
MANY_SNAPSHOTS_COUNT = 4
MANY_SNAPSHOTS_OLDEST_DAYS = 2
OLD_SNAPSHOT_VM = "monitoring01"
OLD_SNAPSHOT_DAYS = 21


def rid(kind: str, name: str) -> str:
    return f"{kind}:{VC}:{name}"


def rel(kind: str, target_id: str) -> dict[str, str]:
    return {"kind": kind, "target_id": target_id}


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def host_fqdn(name: str) -> str:
    return f"{name}.{DOMAIN}"


# --------------------------------------------------------------------- hosts

ESXI_VERSION = "8.0.3"
ESXI_BUILD = "24022510"
HOST_MODEL = "PowerEdge R760"
HOST_VENDOR = "Dell Inc."
HOST_BIOS = "2.3.4"
NTP_SERVERS = [f"ntp1.{DOMAIN}", f"ntp2.{DOMAIN}"]
DNS_SERVERS = ["172.16.11.10", "172.16.11.11"]
LOCKDOWN = "lockdownNormal"

MGMT = f"{VDS}-mgmt"
VMOTION = "pg-vmotion"
VSAN_PG = "pg-vsan"
UPLINK = f"{VDS}-edge-uplink"

# name -> (cluster, cpuMhz, numCpuCores, memoryBytes, uptimeDays, hostIndex)
# hostIndex feeds the last octet of every vmkernel IP and the pnic MACs.
HOSTS: dict[str, tuple[str, int, int, int, int, int]] = {
    "esx01": ("wld01-cl01", 2600, 64, 1024 * GIB, 47, 1),
    "esx02": ("wld01-cl01", 2600, 64, 1024 * GIB, 47, 2),
    "esx03": ("wld01-cl01", 2600, 64, 1024 * GIB, 46, 3),
    "esx04": ("wld01-cl01", 2600, 64, 1024 * GIB, 46, 4),
    "esx05": ("wld01-edge", 2400, 32, 512 * GIB, 33, 5),
    "esx06": ("wld01-edge", 2400, 32, 512 * GIB, 33, 6),
    "esx07": ("wld01-edge", 2400, 32, 512 * GIB, 12, 7),
}


def vmkernel_adapters(idx: int) -> list[dict[str, object]]:
    return [
        {"device": "vmk0", "ip": f"172.16.11.{100 + idx}", "mtu": 1500, "portgroup": MGMT},
        {"device": "vmk1", "ip": f"172.16.12.{100 + idx}", "mtu": 9000, "portgroup": VMOTION},
        {"device": "vmk2", "ip": f"172.16.13.{100 + idx}", "mtu": 9000, "portgroup": VSAN_PG},
    ]


def physical_nics(idx: int) -> list[dict[str, object]]:
    return [
        {"device": "vmnic0", "mac": f"3c:ec:ef:1a:{idx:02x}:00", "linkSpeedMb": 25000},
        {"device": "vmnic1", "mac": f"3c:ec:ef:1a:{idx:02x}:01", "linkSpeedMb": 25000},
    ]


# ------------------------------------------------------------------ clusters

# name -> (drsEnabled, drsAutomationLevel, haEnabled, haAdmissionControl,
#          evcMode, vsanEnabled, ruleCount)
CLUSTERS: dict[str, tuple[bool, str, bool, bool, str, bool, int]] = {
    "wld01-cl01": (True, "fullyAutomated", True, True, "intel-sapphirerapids", True, 2),
    "wld01-edge": (True, "fullyAutomated", True, True, "intel-sapphirerapids", False, 2),
}

# ---------------------------------------------------------------- datastores

# name -> (type, capacity, usedFraction, mountedByCluster, vmfsVersion, url)
DATASTORES: dict[str, tuple[str, int, float, str, str | None, str]] = {
    "wld01-cl01-vsan01": (
        "vsan",
        40 * TIB,
        0.58,
        "wld01-cl01",
        None,
        "ds:///vmfs/volumes/vsan:52a1b2c3d4e5f607-0819a2b3c4d5e6f7/",
    ),
    "wld01-cl01-vsan02": (
        "vsan",
        20 * TIB,
        0.41,
        "wld01-cl01",
        None,
        "ds:///vmfs/volumes/vsan:52a1b2c3d4e5f607-1a2b3c4d5e6f7081/",
    ),
    "wld01-edge-vmfs01": (
        "VMFS",
        8 * TIB,
        0.22,
        "wld01-edge",
        "6.82",
        "ds:///vmfs/volumes/66b0c1d2-3e4f5a6b-7c8d-3cecef1a0500/",
    ),
    "nfs01-iso-templates": (
        "NFS41",
        4 * TIB,
        0.63,
        "*",
        None,
        "ds:///vmfs/volumes/9f8e7d6c-5b4a3928/",
    ),
}

VSAN1 = "wld01-cl01-vsan01"
VSAN2 = "wld01-cl01-vsan02"
EVMFS = "wld01-edge-vmfs01"
NFS = "nfs01-iso-templates"

# ------------------------------------------------------------------ networks

# name -> (type, vlan, numPorts, switch)
NETWORKS: dict[str, tuple[str, object, int | None, str | None]] = {
    MGMT: ("dvportgroup", 1611, 32, VDS),
    VMOTION: ("dvportgroup", 200, 32, VDS),
    VSAN_PG: ("dvportgroup", 300, 32, VDS),
    UPLINK: ("dvportgroup", "trunk 0-4094", 16, VDS),
    "seg-web-10.20.10.0": ("opaque", None, None, None),
    "seg-app-10.20.20.0": ("opaque", None, None, None),
    "seg-db-10.20.30.0": ("opaque", None, None, None),
    "seg-dmz-10.20.40.0": ("opaque", None, None, None),
}

WEB = "seg-web-10.20.10.0"
APP = "seg-app-10.20.20.0"
DB = "seg-db-10.20.30.0"
DMZ = "seg-dmz-10.20.40.0"

# network name -> guest IP prefix (first three octets)
SUBNETS: dict[str, str] = {
    WEB: "10.20.10",
    APP: "10.20.20",
    DB: "10.20.30",
    DMZ: "10.20.40",
    MGMT: "172.16.11",
    UPLINK: "172.16.14",
}

# ----------------------------------------------------------------------- VMs

UBUNTU = "Ubuntu Linux (64-bit)"
PHOTON = "VMware Photon OS (64-bit)"
WIN = "Microsoft Windows Server 2022 (64-bit)"
RHEL = "Red Hat Enterprise Linux 9 (64-bit)"

HW_VERSION = "vmx-21"
TOOLS_VERSION_LINUX = "12448"
TOOLS_VERSION_WIN = "12448"
ROOT_RP = "Resources"


def vm(
    host: str,
    nets: list[str],
    guest: str,
    cpu: int,
    mem_mb: int,
    folder: str,
    ip_octet: int | None,
    *,
    datastore: str = VSAN1,
    power: str = "poweredOn",
    template: bool = False,
    resource_pool: str = ROOT_RP,
    disks: list[tuple[int, bool]] | None = None,
    cpu_res: int = 0,
    mem_res: int = 0,
    annotation: str = "",
) -> dict[str, object]:
    """One row of the VM table. disks is a list of (capacityBytes, thin); the
    default is a single thin OS disk sized for the guest family."""
    if disks is None:
        disks = [(90 * GIB if guest == WIN else 64 * GIB, True)]
    return {
        "host": host,
        "nets": nets,
        "guest": guest,
        "cpu": cpu,
        "mem_mb": mem_mb,
        "folder": folder,
        "ip_octet": ip_octet,
        "datastore": datastore,
        "power": power,
        "template": template,
        "resource_pool": resource_pool,
        "disks": disks,
        "cpu_res": cpu_res,
        "mem_res": mem_res,
        "annotation": annotation,
    }


APP_DISKS = [(64 * GIB, True), (200 * GIB, True)]
DB_DISKS = [(64 * GIB, True), (500 * GIB, False)]
VCLS = {"disks": [(2 * GIB, True)], "cpu_res": 100, "mem_res": 128}
K8S = {"resource_pool": "ws-ns01", "disks": [(16 * GIB, True)]}

# Insertion order is the VM index: it drives boot time offsets and MACs.
VMS: dict[str, dict[str, object]] = {
    # esx01
    "web01": vm("esx01", [WEB], UBUNTU, 2, 4096, "web", 11),
    "web03": vm("esx01", [WEB], UBUNTU, 2, 4096, "web", 13),
    "app01": vm("esx01", [APP], RHEL, 4, 8192, "app", 11, disks=APP_DISKS),
    "db01": vm("esx01", [DB], RHEL, 8, 32768, "db", 11, disks=DB_DISKS, mem_res=16384),
    "dns01": vm("esx01", [MGMT], UBUNTU, 2, 2048, "infra", 10),
    "tpl-ubuntu-2204": vm(
        "esx01",
        [MGMT],
        UBUNTU,
        2,
        2048,
        "templates",
        None,
        datastore=NFS,
        power="poweredOff",
        template=True,
        annotation="Ubuntu 22.04 LTS golden image, built 2026-06-14",
    ),
    "vCLS-wld01-cl01-1": vm("esx01", [], PHOTON, 1, 128, "vCLS", None, **VCLS),
    # esx02
    "web02": vm("esx02", [WEB], UBUNTU, 2, 4096, "web", 12),
    "app02": vm("esx02", [APP], RHEL, 4, 8192, "app", 12, disks=APP_DISKS),
    "dmz-lb01": vm("esx02", [DMZ, WEB], UBUNTU, 2, 4096, "dmz", 21),
    "monitoring01": vm(
        "esx02", [MGMT], UBUNTU, 4, 16384, "infra", 20, disks=[(64 * GIB, True), (400 * GIB, True)]
    ),
    "ws-cp01": vm("esx02", [APP], PHOTON, 4, 16384, "ws-ns01", 50, **K8S),
    "vCLS-wld01-cl01-2": vm("esx02", [], PHOTON, 1, 128, "vCLS", None, **VCLS),
    # esx03
    "web04": vm("esx03", [WEB], UBUNTU, 2, 4096, "web", 14),
    "app03": vm("esx03", [APP], RHEL, 4, 8192, "app", 13, disks=APP_DISKS),
    "db02": vm("esx03", [DB], RHEL, 8, 32768, "db", 12, disks=DB_DISKS, mem_res=16384),
    "dmz-jump01": vm("esx03", [DMZ], WIN, 2, 8192, "dmz", 22),
    "ws-w01": vm("esx03", [APP], PHOTON, 8, 32768, "ws-ns01", 51, **K8S),
    "logs01": vm(
        "esx03",
        [MGMT],
        UBUNTU,
        4,
        16384,
        "infra",
        21,
        datastore=VSAN2,
        disks=[(64 * GIB, True), (1 * TIB, True)],
    ),
    "vCLS-wld01-cl01-3": vm("esx03", [], PHOTON, 1, 128, "vCLS", None, **VCLS),
    # esx04
    "app04": vm("esx04", [APP], RHEL, 4, 8192, "app", 14, disks=APP_DISKS),
    "backup-proxy01": vm(
        "esx04",
        [MGMT],
        WIN,
        4,
        16384,
        "infra",
        22,
        datastore=VSAN2,
        disks=[(90 * GIB, True), (2 * TIB, True)],
    ),
    "ws-w02": vm("esx04", [APP], PHOTON, 8, 32768, "ws-ns01", 52, **K8S),
    "tpl-win2022": vm(
        "esx04",
        [MGMT],
        WIN,
        2,
        4096,
        "templates",
        None,
        datastore=NFS,
        power="poweredOff",
        template=True,
        annotation="Windows Server 2022 golden image, patched 2026-07",
    ),
    # edge cluster (esx07 is spare capacity, no VMs)
    "wld01-en01": vm(
        "esx05",
        [MGMT, UPLINK],
        UBUNTU,
        8,
        32768,
        "nsx-edges",
        31,
        datastore=EVMFS,
        disks=[(200 * GIB, True)],
        mem_res=32768,
    ),
    "vCLS-wld01-edge-1": vm("esx05", [], PHOTON, 1, 128, "vCLS", None, datastore=EVMFS, **VCLS),
    "wld01-en02": vm(
        "esx06",
        [MGMT, UPLINK],
        UBUNTU,
        8,
        32768,
        "nsx-edges",
        32,
        datastore=EVMFS,
        disks=[(200 * GIB, True)],
        mem_res=32768,
    ),
    "vCLS-wld01-edge-2": vm("esx06", [], PHOTON, 1, 128, "vCLS", None, datastore=EVMFS, **VCLS),
}


def host_uptime(hname: str) -> int:
    return HOSTS[hname][4] * 86400


def vm_boot_time(index: int, hname: str) -> str:
    """Deterministic VM boot time: after the host booted, staggered by index."""
    host_boot = BASE_TIME - timedelta(seconds=host_uptime(hname))
    return iso(host_boot + timedelta(hours=6 + index * 3, minutes=index * 7))


def vm_disks(spec: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "label": f"Hard disk {i + 1}",
            "capacityBytes": cap,
            "datastore": spec["datastore"],
            "thin": thin,
        }
        for i, (cap, thin) in enumerate(spec["disks"])
    ]


def vm_nics(index: int, nets: list[str], connected: bool) -> list[dict[str, object]]:
    return [
        {
            "label": f"Network adapter {i + 1}",
            "mac": f"00:50:56:8a:{index:02x}:{i:02x}",
            "network": net,
            "connected": connected,
        }
        for i, net in enumerate(nets)
    ]


def committed_bytes(disks: list[dict[str, object]], powered_on: bool) -> int:
    """Thin disks are about 40% consumed when running, 25% when parked."""
    fraction = 0.40 if powered_on else 0.25
    total = 0
    for d in disks:
        total += int(d["capacityBytes"] * fraction) if d["thin"] else int(d["capacityBytes"])
    return total


def build_snapshot_a() -> list[dict]:
    resources: list[dict] = []

    vc_id = rid("vcenter", VC)
    resources.append(
        {
            "id": vc_id,
            "type": "vcenter",
            "name": VC,
            "source": SOURCE,
            "parent_id": None,
            "properties": {
                "name": "VMware vCenter Server",
                "version": "8.0.3",
                "build": "24322831",
                "apiVersion": "8.0.3.0",
                "instanceUuid": "3f1b6c2a-7d0e-4c5b-9a21-5e8f0c1d2b47",
                "osType": "linux-x64",
            },
            "relationships": [],
        }
    )

    dc_id = rid("datacenter", DATACENTER)
    resources.append(
        {
            "id": dc_id,
            "type": "datacenter",
            "name": DATACENTER,
            "source": SOURCE,
            "parent_id": vc_id,
            "properties": {},
            "relationships": [],
        }
    )

    host_vm_count = {h: 0 for h in HOSTS}
    for spec in VMS.values():
        host_vm_count[spec["host"]] += 1

    for cname, (drs, drs_level, ha, ha_ac, evc, vsan, rules) in CLUSTERS.items():
        members = [h for h, spec in HOSTS.items() if spec[0] == cname]
        resources.append(
            {
                "id": rid("cluster", cname),
                "type": "cluster",
                "name": cname,
                "source": SOURCE,
                "parent_id": dc_id,
                "properties": {
                    "hostCount": len(members),
                    "hosts": sorted(rid("host", h) for h in members),
                    "drsEnabled": drs,
                    "drsAutomationLevel": drs_level,
                    "haEnabled": ha,
                    "haAdmissionControl": ha_ac,
                    "evcMode": evc,
                    "vsanEnabled": vsan,
                    "ruleCount": rules,
                    "totalCpuMhz": sum(HOSTS[h][1] * HOSTS[h][2] for h in members),
                    "totalMemoryBytes": sum(HOSTS[h][3] for h in members),
                    "numVms": sum(host_vm_count[h] for h in members),
                    "overallStatus": "green",
                },
                "relationships": [],
            }
        )

    for dname, (dtype, capacity, used, mounted_by, vmfs, url) in DATASTORES.items():
        mounted = [h for h, spec in HOSTS.items() if mounted_by in ("*", spec[0])]
        resources.append(
            {
                "id": rid("datastore", dname),
                "type": "datastore",
                "name": dname,
                "source": SOURCE,
                "parent_id": dc_id,
                "properties": {
                    "capacity": capacity,
                    "freeSpace": int(capacity * (1 - used)),
                    "accessible": True,
                    "type": dtype,
                    "url": url,
                    "hosts": sorted(host_fqdn(h) for h in mounted),
                    "multipleHostAccess": True,
                    "maintenanceMode": False,
                    "vmfsVersion": vmfs,
                    "overallStatus": "green",
                },
                "relationships": [],
            }
        )

    for nname, (ntype, vlan, ports, switch) in NETWORKS.items():
        resources.append(
            {
                "id": rid("network", nname),
                "type": "network",
                "name": nname,
                "source": SOURCE,
                "parent_id": dc_id,
                "properties": {
                    "type": ntype,
                    "vlan": vlan,
                    "numPorts": ports,
                    "switch": switch,
                    "hosts": None,
                    "exists": True,
                },
                "relationships": [],
            }
        )

    for hname, (cname, mhz, cores, mem, _days, idx) in HOSTS.items():
        rels = [rel("member_of", rid("cluster", cname))]
        mounted = [d for d, spec in DATASTORES.items() if spec[3] in ("*", cname)]
        for dname in mounted:
            rels.append(rel("uses_datastore", rid("datastore", dname)))
        uptime = host_uptime(hname)
        resources.append(
            {
                "id": rid("host", hname),
                "type": "host",
                "name": host_fqdn(hname),
                "source": SOURCE,
                "parent_id": rid("cluster", cname),
                "properties": {
                    "connectionState": "connected",
                    "powerState": "poweredOn",
                    "maintenanceMode": False,
                    "cluster": cname,
                    "datacenter": DATACENTER,
                    "version": ESXI_VERSION,
                    "build": ESXI_BUILD,
                    "model": HOST_MODEL,
                    "vendor": HOST_VENDOR,
                    "biosVersion": HOST_BIOS,
                    "cpuMhz": mhz,
                    "numCpuCores": cores,
                    "memoryBytes": mem,
                    "uptimeSeconds": uptime,
                    "bootTime": iso(BASE_TIME - timedelta(seconds=uptime)),
                    "lockdownMode": LOCKDOWN,
                    "ntpServers": list(NTP_SERVERS),
                    "dnsServers": list(DNS_SERVERS),
                    "vmkernelAdapters": vmkernel_adapters(idx),
                    "physicalNics": physical_nics(idx),
                    "standardSwitches": [],
                    "numVms": host_vm_count[hname],
                    "datastores": sorted(mounted),
                    "overallStatus": "green",
                },
                "relationships": rels,
            }
        )

    for index, (vname, spec) in enumerate(VMS.items()):
        hname = spec["host"]
        cname = HOSTS[hname][0]
        nets = sorted(spec["nets"])
        dss = [spec["datastore"]]
        powered_on = spec["power"] == "poweredOn"
        template = spec["template"]
        rels = [rel("runs_on", rid("host", hname))]
        for n in nets:
            rels.append(rel("uses_network", rid("network", n)))
        for d in dss:
            rels.append(rel("uses_datastore", rid("datastore", d)))
        disks = vm_disks(spec)
        guest = spec["guest"]
        is_vcls = vname.startswith("vCLS")
        if powered_on and nets and spec["ip_octet"] is not None:
            guest_ip = f"{SUBNETS[nets[0]]}.{spec['ip_octet']}"
        else:
            guest_ip = None
        if template:
            tools_status = "toolsNotRunning"
        else:
            tools_status = "toolsOk"
        snapshot_count = 0
        oldest_snapshot = None
        if vname == MANY_SNAPSHOTS_VM:
            snapshot_count = MANY_SNAPSHOTS_COUNT
            oldest_snapshot = iso(BASE_TIME - timedelta(days=MANY_SNAPSHOTS_OLDEST_DAYS))
        elif vname == OLD_SNAPSHOT_VM:
            snapshot_count = 1
            oldest_snapshot = iso(BASE_TIME - timedelta(days=OLD_SNAPSHOT_DAYS))
        resources.append(
            {
                "id": rid("vm", vname),
                "type": "vm",
                "name": vname,
                "source": SOURCE,
                "parent_id": rid("host", hname),
                "properties": {
                    "powerState": spec["power"],
                    "connectionState": "connected",
                    "host": host_fqdn(hname),
                    "cluster": cname,
                    "resourcePool": spec["resource_pool"],
                    "folder": spec["folder"],
                    "guestFullName": guest,
                    "guestHostname": None if (template or is_vcls) else f"{vname}.{DOMAIN}",
                    "guestIp": guest_ip,
                    "guestState": "running" if powered_on else "notRunning",
                    "toolsStatus": tools_status,
                    "toolsVersion": TOOLS_VERSION_WIN if guest == WIN else TOOLS_VERSION_LINUX,
                    "numCpu": spec["cpu"],
                    "memoryMB": spec["mem_mb"],
                    "hardwareVersion": HW_VERSION,
                    "template": template,
                    "cpuReservationMhz": spec["cpu_res"],
                    "memReservationMB": spec["mem_res"],
                    "annotation": spec["annotation"],
                    "snapshotCount": snapshot_count,
                    "oldestSnapshotTime": oldest_snapshot,
                    "disks": disks,
                    "nics": vm_nics(index, nets, powered_on),
                    "networks": nets,
                    "datastores": dss,
                    "storageCommittedBytes": committed_bytes(disks, powered_on),
                    "bootTime": vm_boot_time(index, hname) if powered_on else None,
                    "overallStatus": "green",
                },
                "relationships": rels,
            }
        )

    return resources


def build_snapshot_b(a: list[dict]) -> list[dict]:
    b = copy.deepcopy(a)
    by_id = {r["id"]: r for r in b}

    # 1. Host disconnects (high).
    by_id[rid("host", DISCONNECTED_HOST)]["properties"]["connectionState"] = "disconnected"

    # 2. Host enters maintenance mode (medium).
    by_id[rid("host", MAINTENANCE_HOST)]["properties"]["maintenanceMode"] = True

    # 3. VM migrates between hosts (low). Host VM counts follow.
    vm = by_id[rid("vm", MIGRATED_VM)]
    assert vm["properties"]["host"] == host_fqdn(MIGRATED_FROM)
    vm["properties"]["host"] = host_fqdn(MIGRATED_TO)
    vm["parent_id"] = rid("host", MIGRATED_TO)
    for r in vm["relationships"]:
        if r["kind"] == "runs_on":
            r["target_id"] = rid("host", MIGRATED_TO)
    by_id[rid("host", MIGRATED_FROM)]["properties"]["numVms"] -= 1
    by_id[rid("host", MIGRATED_TO)]["properties"]["numVms"] += 1

    # 4. vSAN datastore fills past 90% (medium, capacity warning).
    ds = by_id[rid("datastore", FULL_DATASTORE)]
    cap = ds["properties"]["capacity"]
    ds["properties"]["freeSpace"] = int(cap * (1 - FULL_DATASTORE_USED_PCT))

    # 5. NSX segment disappears (high), including from VM network lists. The
    #    nic entries keep naming the segment, which is what vCenter shows for
    #    a device whose backing no longer exists.
    net_id = rid("network", REMOVED_NETWORK)
    b = [r for r in b if r["id"] != net_id]
    for r in b:
        if r["type"] != "vm":
            continue
        if REMOVED_NETWORK in r["properties"]["networks"]:
            r["properties"]["networks"] = [
                n for n in r["properties"]["networks"] if n != REMOVED_NETWORK
            ]
            r["relationships"] = [x for x in r["relationships"] if x["target_id"] != net_id]

    # 6. VM powers off (medium). Guest facts go with it.
    off = by_id[rid("vm", POWERED_OFF_VM)]["properties"]
    off["powerState"] = "poweredOff"
    off["guestState"] = "notRunning"
    off["guestIp"] = None
    off["toolsStatus"] = "toolsNotRunning"
    off["bootTime"] = None
    for nic in off["nics"]:
        nic["connected"] = False

    # 7. vMotion vmkernel MTU drops to 1500 on one host (high).
    for vmk in by_id[rid("host", MTU_HOST)]["properties"]["vmkernelAdapters"]:
        if vmk["device"] == MTU_VMK:
            assert vmk["mtu"] != MTU_NEW
            vmk["mtu"] = MTU_NEW

    # 8. vMotion portgroup VLAN retagged (high).
    by_id[rid("network", VLAN_NETWORK)]["properties"]["vlan"] = VLAN_NEW

    # 9. One host loses its second NTP server (medium).
    ntp = by_id[rid("host", NTP_HOST)]["properties"]
    assert NTP_DROPPED in ntp["ntpServers"]
    ntp["ntpServers"] = [s for s in ntp["ntpServers"] if s != NTP_DROPPED]

    # 10. VM gains a disk (medium).
    disk_vm = by_id[rid("vm", DISK_VM)]["properties"]
    disk_vm["disks"].append({**DISK_NEW, "datastore": disk_vm["datastores"][0]})

    # 11. VM memory grows (low).
    by_id[rid("vm", MEMORY_VM)]["properties"]["memoryMB"] = MEMORY_NEW_MB

    # 12. VM renamed, same id (medium).
    by_id[rid("vm", RENAMED_VM)]["name"] = RENAMED_TO

    # 13. Cluster DRS drops to manual (medium).
    by_id[rid("cluster", DRS_CLUSTER)]["properties"]["drsAutomationLevel"] = DRS_NEW_LEVEL

    return b


def write(path: Path, label: str, resources: list[dict]) -> None:
    payload = {"label": label, "resources": resources}
    path.write_text(json.dumps(payload, indent=2) + "\n")


def main() -> None:
    a = build_snapshot_a()
    b = build_snapshot_b(a)
    write(HERE / "snapshot_a.json", "Baseline (healthy)", a)
    write(HERE / "snapshot_b.json", "Degraded (after change)", b)
    print(f"snapshot_a: {len(a)} resources, snapshot_b: {len(b)} resources")


if __name__ == "__main__":
    main()
