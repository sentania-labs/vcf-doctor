# Normalized resource properties (contract)

Every collector emits `Resource.properties` with these camelCase keys. The diff
engine, diagnostics, fixtures, and UI key on them. Missing values are omitted
or `null`; never invent. Byte values are ints. Lists are sorted where order is
not meaningful so diffs are stable.

## vcenter
name, version, build, apiVersion, instanceUuid, osType

## datacenter
(name only)

## cluster
hostCount, hosts (list of host ids), drsEnabled, drsAutomationLevel
(manual|partiallyAutomated|fullyAutomated), haEnabled, haAdmissionControl
(bool), evcMode (str|null), vsanEnabled, ruleCount, totalCpuMhz,
totalMemoryBytes, numVms, overallStatus

## host
connectionState, powerState, maintenanceMode, cluster (name), datacenter,
version, build, model, vendor, biosVersion, cpuMhz, numCpuCores, memoryBytes,
uptimeSeconds, bootTime (iso), lockdownMode (str), ntpServers (list),
dnsServers (list), vmkernelAdapters (list of {device, ip, mtu, portgroup}),
physicalNics (list of {device, mac, linkSpeedMb}), standardSwitches (list of
names), numVms, datastores (list of names), overallStatus

## vm
powerState, connectionState, host (name), cluster (name), resourcePool (name),
folder (name), guestFullName, guestHostname, guestIp, guestState, toolsStatus,
toolsVersion, numCpu, memoryMB, hardwareVersion (e.g. vmx-21), template,
cpuReservationMhz, memReservationMB, annotation, snapshotCount,
oldestSnapshotTime (iso|null), disks (list of {label, capacityBytes,
datastore, thin}), nics (list of {label, mac, network, connected}), networks
(list of names), datastores (list of names), storageCommittedBytes, bootTime,
overallStatus

## datastore
capacity, freeSpace, accessible, type, url, hosts (list of host names),
multipleHostAccess, maintenanceMode, vmfsVersion (str|null), overallStatus

## network
type (standard|dvportgroup|opaque), vlan (int, or "trunk a-b", or
"pvlan N", or null), numPorts, switch (dvs name|null), hosts (list of host
names, standard only), exists (true)

# Diff significance per property

`name` on every type: medium.

| type | high | medium | low |
|---|---|---|---|
| host | connectionState, powerState, vmkernelAdapters | maintenanceMode, cluster, version, build, lockdownMode, ntpServers, dnsServers, bootTime | model, memoryBytes, numCpuCores, physicalNics, standardSwitches |
| vm | | powerState, networks, datastores, disks, nics | host, numCpu, memoryMB, hardwareVersion, template, snapshotCount, resourcePool, folder, cpuReservationMhz, memReservationMB, toolsStatus, guestIp, annotation, bootTime |
| cluster | drsEnabled, haEnabled, vsanEnabled | host membership, drsAutomationLevel, haAdmissionControl, evcMode | ruleCount |
| datastore | accessible, removed | capacity, freeSpace (banded), hosts, maintenanceMode | multipleHostAccess |
| network | removed, vlan | switch | numPorts, hosts |
| vcenter | | version, build | apiVersion |

List-valued properties diff as added/removed items in the summary
("vmkernel vmk1 mtu 1500 -> 9000", "disk Hard disk 3 added"). `bootTime` is
only compared when both sides carry a value (summary "rebooted old -> new").

# New diagnostic checks

VM_SNAPSHOT_STALE (warning: oldest snapshot older than 7 days, or more than
3 snapshots), VM_TOOLS_NOT_RUNNING (info: poweredOn and toolsStatus in
toolsNotRunning/toolsNotInstalled, skip templates), HOST_NTP_NOT_CONFIGURED
(warning: empty ntpServers), CLUSTER_HA_DISABLED (warning), CLUSTER_DRS_DISABLED
(info), HOST_VERSION_MISMATCH (warning: hosts in one cluster on different
version/build).

# Settings

`changes_min_significance` (low|medium|high, default low) in the settings
table, editable on the Settings page; `/api/changes` and the Overview recent
changes honour it via `?min_significance=`.
