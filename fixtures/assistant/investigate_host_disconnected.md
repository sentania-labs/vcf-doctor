## Investigation summary: esx03.wld01.vcf.example disconnected

### What the snapshots show

| Item | Previous snapshot | Current snapshot |
| --- | --- | --- |
| `connectionState` | `connected` | `disconnected` |
| `powerState` | `poweredOn` | `poweredOn` |
| `maintenanceMode` | `false` | `false` |
| Cluster | `wld01-cl01` | `wld01-cl01` |
| Resident VMs | 7 | 7 |

### Affected VMs (still reported as powered on)

- `web04` (segment `seg-web-10.20.10.0`, datastore `wld01-cl01-vsan01`)
- `app03` (segment `seg-app-10.20.20.0`, datastore `wld01-cl01-vsan01`)
- `db02` (segment `seg-db-10.20.30.0`, datastore `wld01-cl01-vsan01`)
- `dmz-jump01` (datastore `wld01-cl01-vsan01`)
- `ws-w01` (segment `seg-app-10.20.20.0`, datastore `wld01-cl01-vsan01`)
- `logs01` (portgroup `wld01-vds01-mgmt`, datastore `wld01-cl01-vsan02`)
- `vCLS-wld01-cl01-3` (datastore `wld01-cl01-vsan01`)

### Datastores this host participates in

- `wld01-cl01-vsan01` (vSAN, accessible)
- `wld01-cl01-vsan02` (vSAN, accessible)
- `nfs01-iso-templates` (NFS 4.1, accessible)

### Related changes in the same interval

Other changes were recorded between the two snapshots. They are listed here so they are not mistaken for effects of the disconnect. None of them involve `esx03`:

- `app02` moved from `esx02.wld01.vcf.example` to `esx04.wld01.vcf.example`.
- `web03` (on `esx01`) changed from `poweredOn` to `poweredOff`.
- `wld01-cl01-vsan01` free space dropped; usage is now about 91%.
- NSX segment `seg-dmz-10.20.40.0` is no longer present. `dmz-jump01` (resident on `esx03`) and `dmz-lb01` previously used it.
- `esx07.wld01.vcf.example` (cluster `wld01-edge`) entered maintenance mode.
- `esx02.wld01.vcf.example` vmk1 (vMotion) MTU dropped from 9000 to 1500.
- Portgroup `pg-vmotion` VLAN changed from 200 to 201.
- `esx04.wld01.vcf.example` lost `ntp2.wld01.vcf.example`; one NTP server remains.
- `app01` gained `Hard disk 3` (100 GiB); `db01` memory grew from 32 GiB to 48 GiB.
- `web02` was renamed `web02-old`.
- Cluster `wld01-edge` DRS automation level changed from fully automated to manual.

### Recommended next probes

1. Read the host's task and event history in vCenter for the minutes around the disconnect.
2. Check whether the host answers on its management address (ICMP, TCP 443, TCP 902).
3. If reachable, query the host directly for `hostd` and `vpxa` service state.
4. Run the vSAN cluster health check for `wld01-cl01` and note any objects with reduced availability.
5. Confirm from inside one of the resident VMs (for example `web04`) that the guest is still serving traffic; the snapshot only carries vCenter's last known power state.

Evidence limits: no host logs, alarms, or network telemetry were supplied. Statements above are based only on the two snapshots.
