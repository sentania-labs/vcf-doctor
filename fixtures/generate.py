"""Deterministic fixture generator for VCF Doctor demo mode.

Produces fixtures/snapshot_a.json (healthy baseline) and
fixtures/snapshot_b.json (degraded) describing one fictional VCF workload
domain. Run from anywhere:

    python fixtures/generate.py

Every value is hard coded or derived from a fixed table, so re-running the
script yields byte-identical output. Tweak the tables, re-run, and commit.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

VC = "vc-wld01"
SOURCE = f"vcenter:{VC}"
DOMAIN = "wld01.vcf.example"

TIB = 1024**4
GIB = 1024**3

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


def rid(kind: str, name: str) -> str:
    return f"{kind}:{VC}:{name}"


def rel(kind: str, target_id: str) -> dict[str, str]:
    return {"kind": kind, "target_id": target_id}


# name -> (cluster, cpuMhz, numCpuCores, memoryBytes)
HOSTS: dict[str, tuple[str, int, int, int]] = {
    "esx01": ("wld01-cl01", 2600, 64, 1024 * GIB),
    "esx02": ("wld01-cl01", 2600, 64, 1024 * GIB),
    "esx03": ("wld01-cl01", 2600, 64, 1024 * GIB),
    "esx04": ("wld01-cl01", 2600, 64, 1024 * GIB),
    "esx05": ("wld01-edge", 2400, 32, 512 * GIB),
    "esx06": ("wld01-edge", 2400, 32, 512 * GIB),
    "esx07": ("wld01-edge", 2400, 32, 512 * GIB),
}
ESXI_VERSION = "8.0.3"
ESXI_BUILD = "24280767"

# name -> (drsEnabled, haEnabled, vsanEnabled)
CLUSTERS: dict[str, tuple[bool, bool, bool]] = {
    "wld01-cl01": (True, True, True),
    "wld01-edge": (True, True, True),
}

# name -> (type, capacity, usedFraction, mountedByCluster)
DATASTORES: dict[str, tuple[str, int, float, str]] = {
    "wld01-cl01-vsan01": ("vsan", 40 * TIB, 0.58, "wld01-cl01"),
    "wld01-cl01-vsan02": ("vsan", 20 * TIB, 0.41, "wld01-cl01"),
    "wld01-edge-vsan01": ("vsan", 8 * TIB, 0.22, "wld01-edge"),
    "nfs01-iso-templates": ("NFS41", 4 * TIB, 0.63, "*"),
}

# name -> (type, extra properties)
NETWORKS: dict[str, tuple[str, dict[str, object]]] = {
    "wld01-vds01-mgmt": ("DistributedVirtualPortgroup", {"vlanId": 1611}),
    "wld01-vds01-edge-uplink": ("DistributedVirtualPortgroup", {"vlanId": 1614}),
    "seg-web-10.20.10.0": ("NsxSegment", {"transportZone": "wld01-overlay-tz"}),
    "seg-app-10.20.20.0": ("NsxSegment", {"transportZone": "wld01-overlay-tz"}),
    "seg-db-10.20.30.0": ("NsxSegment", {"transportZone": "wld01-overlay-tz"}),
    "seg-dmz-10.20.40.0": ("NsxSegment", {"transportZone": "wld01-overlay-tz"}),
}

MGMT = "wld01-vds01-mgmt"
UPLINK = "wld01-vds01-edge-uplink"
WEB = "seg-web-10.20.10.0"
APP = "seg-app-10.20.20.0"
DB = "seg-db-10.20.30.0"
DMZ = "seg-dmz-10.20.40.0"
VSAN1 = "wld01-cl01-vsan01"
VSAN2 = "wld01-cl01-vsan02"
EVSAN = "wld01-edge-vsan01"
NFS = "nfs01-iso-templates"

UBUNTU = "Ubuntu Linux (64-bit)"
PHOTON = "VMware Photon OS (64-bit)"
WIN = "Microsoft Windows Server 2022 (64-bit)"
RHEL = "Red Hat Enterprise Linux 9 (64-bit)"

# name -> (host, networks, datastores, guestFullName, numCpu, memoryMB,
#          powerState, template)
VMS: dict[str, tuple[str, list[str], list[str], str, int, int, str, bool]] = {
    # esx01
    "web01": ("esx01", [WEB], [VSAN1], UBUNTU, 2, 4096, "poweredOn", False),
    "web03": ("esx01", [WEB], [VSAN1], UBUNTU, 2, 4096, "poweredOn", False),
    "app01": ("esx01", [APP], [VSAN1], RHEL, 4, 8192, "poweredOn", False),
    "db01": ("esx01", [DB], [VSAN1], RHEL, 8, 32768, "poweredOn", False),
    "dns01": ("esx01", [MGMT], [VSAN1], UBUNTU, 2, 2048, "poweredOn", False),
    "tpl-ubuntu-2204": ("esx01", [MGMT], [NFS], UBUNTU, 2, 2048, "poweredOff", True),
    "vCLS-wld01-cl01-1": ("esx01", [], [VSAN1], PHOTON, 1, 128, "poweredOn", False),
    # esx02
    "web02": ("esx02", [WEB], [VSAN1], UBUNTU, 2, 4096, "poweredOn", False),
    "app02": ("esx02", [APP], [VSAN1], RHEL, 4, 8192, "poweredOn", False),
    "dmz-lb01": ("esx02", [DMZ, WEB], [VSAN1], UBUNTU, 2, 4096, "poweredOn", False),
    "monitoring01": ("esx02", [MGMT], [VSAN1], UBUNTU, 4, 16384, "poweredOn", False),
    "ws-cp01": ("esx02", [APP], [VSAN1], PHOTON, 4, 16384, "poweredOn", False),
    "vCLS-wld01-cl01-2": ("esx02", [], [VSAN1], PHOTON, 1, 128, "poweredOn", False),
    # esx03
    "web04": ("esx03", [WEB], [VSAN1], UBUNTU, 2, 4096, "poweredOn", False),
    "app03": ("esx03", [APP], [VSAN1], RHEL, 4, 8192, "poweredOn", False),
    "db02": ("esx03", [DB], [VSAN1], RHEL, 8, 32768, "poweredOn", False),
    "dmz-jump01": ("esx03", [DMZ], [VSAN1], WIN, 2, 8192, "poweredOn", False),
    "ws-w01": ("esx03", [APP], [VSAN1], PHOTON, 8, 32768, "poweredOn", False),
    "logs01": ("esx03", [MGMT], [VSAN2], UBUNTU, 4, 16384, "poweredOn", False),
    "vCLS-wld01-cl01-3": ("esx03", [], [VSAN1], PHOTON, 1, 128, "poweredOn", False),
    # esx04
    "app04": ("esx04", [APP], [VSAN1], RHEL, 4, 8192, "poweredOn", False),
    "backup-proxy01": ("esx04", [MGMT], [VSAN2], WIN, 4, 16384, "poweredOn", False),
    "harbor01": ("esx04", [WEB], [VSAN2], UBUNTU, 4, 8192, "poweredOn", False),
    "ws-w02": ("esx04", [APP], [VSAN1], PHOTON, 8, 32768, "poweredOn", False),
    "ops-proxy01": ("esx04", [MGMT], [VSAN1], PHOTON, 2, 4096, "poweredOn", False),
    "tpl-win2022": ("esx04", [MGMT], [NFS], WIN, 2, 4096, "poweredOff", True),
    # edge cluster (esx07 is spare capacity, no VMs)
    "wld01-en01": ("esx05", [MGMT, UPLINK], [EVSAN], UBUNTU, 8, 32768, "poweredOn", False),
    "vCLS-wld01-edge-1": ("esx05", [], [EVSAN], PHOTON, 1, 128, "poweredOn", False),
    "wld01-en02": ("esx06", [MGMT, UPLINK], [EVSAN], UBUNTU, 8, 32768, "poweredOn", False),
    "vCLS-wld01-edge-2": ("esx06", [], [EVSAN], PHOTON, 1, 128, "poweredOn", False),
}


def host_fqdn(name: str) -> str:
    return f"{name}.{DOMAIN}"


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
                "version": "8.0.3",
                "build": "24322831",
                "instanceUuid": "3f1b6c2a-7d0e-4c5b-9a21-5e8f0c1d2b47",
                "apiType": "VirtualCenter",
            },
            "relationships": [],
        }
    )

    dc_id = rid("datacenter", "wld01-dc")
    resources.append(
        {
            "id": dc_id,
            "type": "datacenter",
            "name": "wld01-dc",
            "source": SOURCE,
            "parent_id": vc_id,
            "properties": {},
            "relationships": [],
        }
    )

    for cname, (drs, ha, vsan) in CLUSTERS.items():
        host_count = sum(1 for h in HOSTS.values() if h[0] == cname)
        resources.append(
            {
                "id": rid("cluster", cname),
                "type": "cluster",
                "name": cname,
                "source": SOURCE,
                "parent_id": dc_id,
                "properties": {
                    "hostCount": host_count,
                    "drsEnabled": drs,
                    "haEnabled": ha,
                    "vsanEnabled": vsan,
                },
                "relationships": [],
            }
        )

    for dname, (dtype, capacity, used, _) in DATASTORES.items():
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
                },
                "relationships": [],
            }
        )

    for nname, (ntype, extra) in NETWORKS.items():
        resources.append(
            {
                "id": rid("network", nname),
                "type": "network",
                "name": nname,
                "source": SOURCE,
                "parent_id": dc_id,
                "properties": {"type": ntype, **extra},
                "relationships": [],
            }
        )

    for hname, (cname, mhz, cores, mem) in HOSTS.items():
        rels = [rel("member_of", rid("cluster", cname))]
        for dname, (_, _, _, mounted_by) in DATASTORES.items():
            if mounted_by in ("*", cname):
                rels.append(rel("uses_datastore", rid("datastore", dname)))
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
                    "cpuMhz": mhz,
                    "numCpuCores": cores,
                    "memoryBytes": mem,
                    "version": ESXI_VERSION,
                    "build": ESXI_BUILD,
                },
                "relationships": rels,
            }
        )

    for vname, (hname, nets, dss, guest, cpu, mem_mb, power, template) in VMS.items():
        cname = HOSTS[hname][0]
        rels = [rel("runs_on", rid("host", hname))]
        seen: set[str] = set()
        for n in nets:
            if n not in seen:
                seen.add(n)
                rels.append(rel("uses_network", rid("network", n)))
        for d in dss:
            rels.append(rel("uses_datastore", rid("datastore", d)))
        resources.append(
            {
                "id": rid("vm", vname),
                "type": "vm",
                "name": vname,
                "source": SOURCE,
                "parent_id": rid("host", hname),
                "properties": {
                    "powerState": power,
                    "host": host_fqdn(hname),
                    "cluster": cname,
                    "networks": list(nets),
                    "datastores": list(dss),
                    "guestFullName": guest,
                    "numCpu": cpu,
                    "memoryMB": mem_mb,
                    "template": template,
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

    # 3. VM migrates between hosts (low).
    vm = by_id[rid("vm", MIGRATED_VM)]
    assert vm["properties"]["host"] == host_fqdn(MIGRATED_FROM)
    vm["properties"]["host"] = host_fqdn(MIGRATED_TO)
    vm["parent_id"] = rid("host", MIGRATED_TO)
    for r in vm["relationships"]:
        if r["kind"] == "runs_on":
            r["target_id"] = rid("host", MIGRATED_TO)

    # 4. vSAN datastore fills past 90% (capacity warning).
    ds = by_id[rid("datastore", FULL_DATASTORE)]
    cap = ds["properties"]["capacity"]
    ds["properties"]["freeSpace"] = int(cap * (1 - FULL_DATASTORE_USED_PCT))

    # 5. NSX segment disappears (high), including from VM network lists.
    net_id = rid("network", REMOVED_NETWORK)
    b = [r for r in b if r["id"] != net_id]
    for r in b:
        if r["type"] != "vm":
            continue
        if REMOVED_NETWORK in r["properties"]["networks"]:
            r["properties"]["networks"] = [
                n for n in r["properties"]["networks"] if n != REMOVED_NETWORK
            ]
            r["relationships"] = [
                x for x in r["relationships"] if x["target_id"] != net_id
            ]

    # 6. VM powers off (medium).
    by_id[rid("vm", POWERED_OFF_VM)]["properties"]["powerState"] = "poweredOff"

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
