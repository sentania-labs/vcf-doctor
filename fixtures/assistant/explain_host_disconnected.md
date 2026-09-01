## Observed facts

- vCenter `vc-wld01` reports host `esx03.wld01.vcf.example` with `connectionState: disconnected` in the current snapshot. The previous snapshot showed it `connected`.
- The host is a member of cluster `wld01-cl01` (4 hosts, DRS enabled, HA enabled, vSAN enabled).
- The host still reports `powerState: poweredOn` and `maintenanceMode: false`. Those values did not change between snapshots.
- Seven VMs list this host as their `host`: `web04`, `app03`, `db02`, `dmz-jump01`, `ws-w01`, `logs01`, and `vCLS-wld01-cl01-3`. All seven still report `powerState: poweredOn` in the current snapshot.
- The host has `uses_datastore` relationships to `wld01-cl01-vsan01`, `wld01-cl01-vsan02`, and `nfs01-iso-templates`. All three datastores still report `accessible: true`.

## Inferences

- A `disconnected` state means vCenter has lost its management connection to the host agent. It does not by itself mean the host is down: the power state in the evidence is the last value vCenter recorded, not a live reading.
- Because the VM power states are unchanged, the most likely cause is a management-plane problem (host agent, management network, or vCenter to host communication) rather than a host crash. A crashed host in an HA cluster would normally be followed by VM restarts on other hosts, and no such migrations appear in the evidence.
- The vSAN datastore `wld01-cl01-vsan01` is one of four contributors in this cluster. Losing one host reduces vSAN redundancy for objects with components on that host while the host is unreachable.

## Suggested investigation

1. Confirm the state directly in vCenter and check the host's recent tasks and events for the disconnect time.
2. Test management-network reachability to the host from vCenter (ping, port 443, port 902).
3. If the host is reachable, check the state of the host agent (`hostd`) and `vpxa`.
4. Check vSAN health for the cluster to see whether the host's disk group is contributing.

## Suggested remediation

- If the host is reachable and the agents are healthy, reconnect the host from vCenter.
- If the host is unreachable on the management network, treat this as a network incident first; do not restart the host while its seven VMs are still running.

The evidence does not include host logs, alarms, or network telemetry, so the cause cannot be confirmed from this data alone.
