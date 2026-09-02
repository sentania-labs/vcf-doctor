# VCF Doctor fixtures

Deterministic test data for the fixture collector (backend tests and the CI
smoke test; not an operator feature). Two snapshots of one
fictional VCF workload domain, plus canned assistant answers for the mock
provider. Nothing here refers to a real environment: the domain
`wld01.vcf.example` and every name in it are invented.

## Files

| Path | Purpose |
| --- | --- |
| `generate.py` | Produces both snapshot files from fixed tables. Re-run after any tweak. |
| `snapshot_a.json` | Healthy baseline. 51 resources. |
| `snapshot_b.json` | Degraded state after fifteen changes. 50 resources. |
| `assistant/*.md` | Reference answers grounded in this data. The mock provider synthesizes its own output from the request; these document what a good answer looks like. |

Validation lives in `backend/tests/test_fixtures_valid.py`. It parses every
resource with the frozen `Resource` model, checks id uniqueness and reference
integrity, asserts every resource carries every key the contract lists for
its type, asserts the exact A to B delta, and fails if the JSON on disk no
longer matches what `generate.py` produces.

## Schema

Each snapshot file is one object:

```json
{"label": "Baseline (healthy)", "resources": [ ...Resource ]}
```

`Resource` matches `backend/app/models/resource.py` exactly:

| Field | Notes |
| --- | --- |
| `id` | `<type>:vc-wld01:<name>`, stable across both files (a rename keeps its id) |
| `type` | `vcenter`, `datacenter`, `cluster`, `host`, `vm`, `datastore`, `network` |
| `name` | Display name. Hosts use the FQDN, everything else the short name |
| `source` | Always `vcenter:vc-wld01` |
| `parent_id` | vm -> host -> cluster -> datacenter -> vcenter; datastores and networks parent to the datacenter |
| `properties` | camelCase keys per `docs/PROPERTIES.md`, listed below |
| `relationships` | `{kind, target_id}` edges |

## Property coverage

Every resource carries every key `docs/PROPERTIES.md` lists for its type.
Values that do not apply are `null`, never omitted.

- vcenter: `name` ("VMware vCenter Server"), `version` 8.0.3, `build`,
  `apiVersion`, `instanceUuid`, `osType` linux-x64
- datacenter: no properties
- cluster: `hostCount`, `hosts` (host ids, sorted), `drsEnabled`,
  `drsAutomationLevel` fullyAutomated, `haEnabled`, `haAdmissionControl` true,
  `evcMode` intel-sapphirerapids, `vsanEnabled` (true on `wld01-cl01`, false on
  `wld01-edge`), `ruleCount` 2, `totalCpuMhz`, `totalMemoryBytes`, `numVms`,
  `overallStatus`
- host: `connectionState`, `powerState`, `maintenanceMode`, `cluster`,
  `datacenter`, `version` 8.0.3, `build` 24022510, `model` PowerEdge R760,
  `vendor` Dell Inc., `biosVersion`, `cpuMhz`, `numCpuCores`, `memoryBytes`,
  `uptimeSeconds`, `bootTime`, `lockdownMode` lockdownNormal, `ntpServers`
  (`ntp1` and `ntp2.wld01.vcf.example`), `dnsServers`, `vmkernelAdapters`
  (vmk0 mgmt mtu 1500 on `wld01-vds01-mgmt`, vmk1 vMotion mtu 9000 on
  `pg-vmotion`, vmk2 vSAN mtu 9000 on `pg-vsan`), `physicalNics` (vmnic0 and
  vmnic1, 25 GbE), `standardSwitches` (empty, vDS only), `numVms`,
  `datastores` (names), `overallStatus`
- vm: `powerState`, `connectionState`, `host` (FQDN), `cluster`,
  `resourcePool`, `folder`, `guestFullName`, `guestHostname`, `guestIp`,
  `guestState`, `toolsStatus`, `toolsVersion`, `numCpu`, `memoryMB`,
  `hardwareVersion` vmx-21, `template`, `cpuReservationMhz`,
  `memReservationMB`, `annotation`, `snapshotCount`, `oldestSnapshotTime`,
  `disks` (`{label, capacityBytes, datastore, thin}`), `nics`
  (`{label, mac, network, connected}`), `networks` (names, sorted),
  `datastores` (names), `storageCommittedBytes`, `bootTime`, `overallStatus`
- datastore: `capacity`, `freeSpace` (bytes, ints), `accessible`, `type`
  (`vsan`, `VMFS`, `NFS41`), `url`, `hosts` (host FQDNs, sorted),
  `multipleHostAccess`, `maintenanceMode`, `vmfsVersion` (6.82 on the VMFS
  datastore, null on vSAN and NFS), `overallStatus`
- network: `type` (`dvportgroup` or `opaque`), `vlan` (int, `"trunk 0-4094"`
  on the edge uplink, null on NSX segments), `numPorts`, `switch`
  (`wld01-vds01` or null), `hosts` (null, standard switches only), `exists`

Timestamps are offsets from a fixed instant, `BASE_TIME` = 2026-08-31T06:00:00Z
in `generate.py`, never from the wall clock, so both files are byte-stable.

Relationships: vm `runs_on` host, vm `uses_network` network, vm `uses_datastore`
datastore, host `member_of` cluster, host `uses_datastore` datastore. A VM's
`networks` and `datastores` property lists are kept in the same order as its
relationship edges.

## Inventory (snapshot A)

- vCenter `vc-wld01` 8.0.3, datacenter `wld01-dc`
- Cluster `wld01-cl01` (vSAN, DRS fully automated, HA with admission control,
  EVC Sapphire Rapids): `esx01` to `esx04`, 64 cores and 1 TiB each, 24 VMs
- Cluster `wld01-edge` (shared VMFS, same DRS/HA/EVC): `esx05` to `esx07`, 32
  cores and 512 GiB each. NSX edge nodes `wld01-en01` and `wld01-en02` on esx05
  and esx06; esx07 is spare capacity with no VMs
- 28 VMs: web01 to web04, app01 to app04, db01 and db02, a DMZ load balancer
  and jump host, a small Kubernetes footprint (`ws-cp01`, `ws-w01`, `ws-w02` in
  resource pool `ws-ns01`), infrastructure VMs (dns01, monitoring01, logs01,
  backup-proxy01), two templates, five vCLS VMs, two NSX edge nodes
- Snapshot outliers (identical in A and B, so `VM_SNAPSHOT_STALE` fires in
  both): `backup-proxy01` has 4 snapshots, oldest 2 days before BASE_TIME;
  `monitoring01` has 1 snapshot, 21 days before BASE_TIME
- Datastores: `wld01-cl01-vsan01` (40 TiB, 58% used), `wld01-cl01-vsan02`
  (20 TiB), `wld01-edge-vmfs01` (8 TiB VMFS 6.82), `nfs01-iso-templates`
  (4 TiB NFS 4.1)
- Networks: vDS `wld01-vds01` portgroups `wld01-vds01-mgmt` (VLAN 1611),
  `pg-vmotion` (VLAN 200), `pg-vsan` (VLAN 300), `wld01-vds01-edge-uplink`
  (trunk); NSX segments `seg-web-10.20.10.0`, `seg-app-10.20.20.0`,
  `seg-db-10.20.30.0`, `seg-dmz-10.20.40.0`

Nothing in A trips the empty-NTP, HA-off, DRS-off, tools-not-running or
version-mismatch checks: every host has two NTP servers and the same build,
every powered-on VM reports `toolsOk`.

## A to B change list

Snapshot B is a deep copy of A with exactly these edits. Every other resource is
identical. Significance follows the table in `docs/PROPERTIES.md`; one Change
per changed resource, so the diff of A to B is 15 changes (4 high, 9 medium,
2 low).

| # | Resource id | Change | Expected significance |
| --- | --- | --- | --- |
| 1 | `host:vc-wld01:esx03` | `connectionState` connected -> disconnected | high |
| 2 | `network:vc-wld01:seg-dmz-10.20.40.0` | removed | high |
| 3 | `host:vc-wld01:esx02` | `vmkernelAdapters` vmk1 mtu 9000 -> 1500 (`numVms` 6 -> 5 because app02 left, untracked) | high |
| 4 | `network:vc-wld01:pg-vmotion` | `vlan` 200 -> 201 | high |
| 5 | `vm:vc-wld01:dmz-lb01` | `seg-dmz-10.20.40.0` dropped from `networks` and the `uses_network` edge removed (consequence of 2); the nic entry still names the segment, as vCenter shows for a device whose backing is gone | medium |
| 6 | `vm:vc-wld01:dmz-jump01` | same as 5 | medium |
| 7 | `datastore:vc-wld01:wld01-cl01-vsan01` | `freeSpace` drops; usage 58% -> 91% (capacity warning) | medium |
| 8 | `vm:vc-wld01:web03` | `powerState` poweredOn -> poweredOff; `guestState`, `guestIp`, `toolsStatus`, `bootTime` and nic `connected` follow | medium |
| 9 | `host:vc-wld01:esx07` | `maintenanceMode` false -> true | medium |
| 10 | `host:vc-wld01:esx04` | `ntpServers` loses `ntp2.wld01.vcf.example` (`numVms` 4 -> 5 because app02 arrived, untracked) | medium |
| 11 | `vm:vc-wld01:app01` | `disks` gains `Hard disk 3`, 100 GiB thin on `wld01-cl01-vsan01` | medium |
| 12 | `vm:vc-wld01:web02` | `name` web02 -> web02-old, same id, properties untouched | medium |
| 13 | `cluster:vc-wld01:wld01-edge` | `drsAutomationLevel` fullyAutomated -> manual | medium |
| 14 | `vm:vc-wld01:app02` | `host` esx02 -> esx04; `parent_id` and `runs_on` follow | low |
| 15 | `vm:vc-wld01:db01` | `memoryMB` 32768 -> 49152 | low |

The seven VMs resident on `esx03` (`web04`, `app03`, `db02`, `dmz-jump01`,
`ws-w01`, `logs01`, `vCLS-wld01-cl01-3`) keep their last known state in B
(dmz-jump01 changes only because of the segment removal), which is what a
management-plane disconnect looks like from vCenter.

Expected findings on B: `HOST_DISCONNECTED` (esx03, critical),
`NETWORK_REMOVED` (seg-dmz, critical), `DATASTORE_HIGH_USAGE` (vsan01,
warning), `HOST_MAINTENANCE_MODE` (esx07, warning), `VM_POWERED_OFF` (web03,
info), `VM_SNAPSHOT_STALE` (backup-proxy01 and monitoring01, warning).
`HOST_NTP_NOT_CONFIGURED` does not fire: esx04 still has one server.

## Assistant fixtures

`assistant/` holds reference answers grounded in the fixture data above.
The mock provider does not read them; it builds answers from the request.
They exist as a written standard for what an evidence-grounded answer
looks like.

- `explain_host_disconnected.md`: explain task for `HOST_DISCONNECTED` on esx03
- `investigate_host_disconnected.md`: investigate task, includes the other
  changes in the interval so they are not blamed on the disconnect
- `script_powercli_host_disconnected.md`: PowerCLI with the two headings the
  backend expects, `## Investigation (read only)` and
  `## Modification (changes environment)`
- `fallback.md`: generic answer when no canned response matches

## Regenerating

```sh
python fixtures/generate.py
cd backend && uv run pytest -q tests/test_fixtures_*
```

Change knobs (which host disconnects, which VM migrates, datastore fill level,
which vmk loses its MTU, and so on) are constants at the top of `generate.py`.
The test file pins the same ids, so update both when changing a knob.
