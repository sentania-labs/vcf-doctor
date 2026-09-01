# VCF Doctor fixtures

Deterministic demo data for `VCF_DOCTOR_DEMO_MODE=true`. Two snapshots of one
fictional VCF workload domain, plus canned assistant answers for the mock
provider. Nothing here refers to a real environment: the domain
`wld01.vcf.example` and every name in it are invented.

## Files

| Path | Purpose |
| --- | --- |
| `generate.py` | Produces both snapshot files from fixed tables. Re-run after any tweak. |
| `snapshot_a.json` | Healthy baseline. 51 resources. |
| `snapshot_b.json` | Degraded state after six changes. 50 resources. |
| `assistant/*.md` | Reference answers grounded in this data. The mock provider synthesizes its own output from the request; these document what a good answer looks like. |

Validation lives in `backend/tests/test_fixtures_valid.py`. It parses every
resource with the frozen `Resource` model, checks id uniqueness and reference
integrity, asserts the exact A to B delta, and fails if the JSON on disk no
longer matches what `generate.py` produces.

## Schema

Each snapshot file is one object:

```json
{"label": "Baseline (healthy)", "resources": [ ...Resource ]}
```

`Resource` matches `backend/app/models/resource.py` exactly:

| Field | Notes |
| --- | --- |
| `id` | `<type>:vc-wld01:<name>`, stable across both files |
| `type` | `vcenter`, `datacenter`, `cluster`, `host`, `vm`, `datastore`, `network` |
| `name` | Display name. Hosts use the FQDN, everything else the short name |
| `source` | Always `vcenter:vc-wld01` |
| `parent_id` | vm -> host -> cluster -> datacenter -> vcenter; datastores and networks parent to the datacenter |
| `properties` | camelCase keys, listed below |
| `relationships` | `{kind, target_id}` edges |

Properties per type:

- host: `connectionState`, `powerState`, `maintenanceMode`, `cluster`, `cpuMhz`, `numCpuCores`, `memoryBytes`, `version`, `build`
- vm: `powerState`, `host` (host FQDN), `cluster`, `networks` (names), `datastores` (names), `guestFullName`, `numCpu`, `memoryMB`, `template`, `overallStatus`
- datastore: `capacity`, `freeSpace` (bytes, ints), `accessible`, `type` (`vsan` or `NFS41`)
- cluster: `hostCount`, `drsEnabled`, `haEnabled`, `vsanEnabled`
- network: `type` (`DistributedVirtualPortgroup` or `NsxSegment`) plus `vlanId` or `transportZone`
- vcenter: `version`, `build`, `instanceUuid`, `apiType`

Relationships: vm `runs_on` host, vm `uses_network` network, vm `uses_datastore`
datastore, host `member_of` cluster, host `uses_datastore` datastore. A VM's
`networks` and `datastores` property lists are kept in the same order as its
relationship edges.

## Inventory (snapshot A)

- vCenter `vc-wld01` 8.0.3, datacenter `wld01-dc`
- Cluster `wld01-cl01`: `esx01` to `esx04`, 64 cores and 1 TiB each, 26 VMs
- Cluster `wld01-edge`: `esx05` to `esx07`, 32 cores and 512 GiB each. NSX edge
  nodes `wld01-en01` and `wld01-en02` on esx05 and esx06; esx07 is spare
  capacity with no VMs
- 30 VMs: web01 to web04, app01 to app04, db01 and db02, a DMZ load balancer and
  jump host, a small Kubernetes footprint (`ws-cp01`, `ws-w01`, `ws-w02`),
  infrastructure VMs (dns, monitoring, logs, backup proxy, harbor, ops proxy),
  two templates, five vCLS VMs, two NSX edge nodes
- Datastores: `wld01-cl01-vsan01` (40 TiB, 58% used), `wld01-cl01-vsan02`
  (20 TiB), `wld01-edge-vsan01` (8 TiB), `nfs01-iso-templates` (4 TiB NFS 4.1)
- Networks: vDS portgroups `wld01-vds01-mgmt` and `wld01-vds01-edge-uplink`;
  NSX segments `seg-web-10.20.10.0`, `seg-app-10.20.20.0`, `seg-db-10.20.30.0`,
  `seg-dmz-10.20.40.0`

## A to B change list

Snapshot B is a deep copy of A with exactly these edits. Every other resource is
identical.

| # | Resource id | Change | Expected significance |
| --- | --- | --- | --- |
| 1 | `host:vc-wld01:esx03` | `connectionState` connected -> disconnected | high |
| 2 | `network:vc-wld01:seg-dmz-10.20.40.0` | removed | high |
| 3 | `vm:vc-wld01:dmz-lb01`, `vm:vc-wld01:dmz-jump01` | `seg-dmz-10.20.40.0` dropped from `networks` and the `uses_network` edge removed (consequence of 2) | high (network removed) |
| 4 | `datastore:vc-wld01:wld01-cl01-vsan01` | `freeSpace` drops; usage 58% -> 91% | warning (capacity check) |
| 5 | `vm:vc-wld01:web03` | `powerState` poweredOn -> poweredOff | medium |
| 6 | `host:vc-wld01:esx07` | `maintenanceMode` false -> true | medium |
| 7 | `vm:vc-wld01:app02` | `host` esx02 -> esx04; `parent_id` and `runs_on` follow | low |

The seven VMs resident on `esx03` (`web04`, `app03`, `db02`, `dmz-jump01`,
`ws-w01`, `logs01`, `vCLS-wld01-cl01-3`) are unchanged in B, which is what a
management-plane disconnect looks like from vCenter.

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
and so on) are constants at the top of `generate.py`. The test file pins the
same ids, so update both when changing a knob.
