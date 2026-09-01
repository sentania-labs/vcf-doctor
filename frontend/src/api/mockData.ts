// Realistic mock estate: a VCF workload domain with two clusters, six hosts, thirty VMs,
// vSAN datastores and NSX segments, one disconnected host, one datastore at 91%.
import type {
  Change, ChangeLogEntry, ConnectionPublic, Event, EventCategory, EventSource, Finding, Resource, ScanRun, Schedule, SnapshotSummary, SnapshotTier, Settings, AssistantStatus,
} from '@/types'

const now = Date.now()
const minutesAgo = (m: number) => new Date(now - m * 60_000).toISOString()
const hoursAgo = (h: number) => minutesAgo(h * 60)

// Scheduled snapshot series shaped like the retention policy leaves it: every scan for the
// last few hours (recent), one per few hours for the last two days (hourly), one per day
// after that (daily). Enough rows (> 30) to exercise the grouped pickers and their filter.
function tieredSnapshots(src: string, idPrefix: string, resourceCount: number, startSeq: number): SnapshotSummary[] {
  const out: SnapshotSummary[] = []
  let seq = startSeq
  const add = (minAgo: number, tier: SnapshotTier, label = 'Scheduled', count = resourceCount) => {
    out.push({ id: `${idPrefix}-${String(seq--).padStart(3, '0')}`, created_at: minutesAgo(minAgo), label, connection_id: src, scheduled: true, resource_count: count, tier })
  }
  for (let m = 102; m <= 12 * 60; m += 40) add(m, 'recent', 'Scheduled', resourceCount - 1)            // ~17 rows over the last 12 h
  for (let h = 15; h <= 48; h += 3) add(h * 60, 'hourly', 'Scheduled', resourceCount - 1)              // 12 rows, hourly tier
  for (let d = 3; d <= 16; d++) add(d * 1440 + 17, 'daily', 'Scheduled', resourceCount - 2)             // 14 rows, daily tier
  return out
}

export interface MockEstate {
  connection: ConnectionPublic
  schedule: Schedule
  resources: Resource[]
  findings: Finding[]
  snapshots: SnapshotSummary[]
  changes: Change[]
  changeLog: ChangeLogEntry[]
  events: Event[]
  scans: ScanRun[]
}

function buildWorkloadDomain(): MockEstate {
  const src = 'vc01'
  const id = (type: string, name: string) => `${type}:${src}:${name}`
  const r: Resource[] = []
  const push = (res: Resource) => { r.push(res); return res }

  const vc = push({
    id: id('vcenter', 'vc01'), type: 'vcenter', name: 'vc-wld01.wld01.vcf.example', source: src, parent_id: null,
    properties: { name: 'vc-wld01.wld01.vcf.example', version: '9.0.1', build: '24805960', apiVersion: '9.0.1.0', instanceUuid: 'c9b2c5d0-2f0e-4a4e-9f8f-1a2b3c4d5e6f', osType: 'linux-x64' },
    relationships: [],
  })
  const dc = push({
    id: id('datacenter', 'wld01-dc'), type: 'datacenter', name: 'wld01-dc', source: src, parent_id: vc.id,
    properties: { moref: 'datacenter-3' }, relationships: [],
  })
  const clusters = [
    { name: 'wld01-cl01', ha: true, drs: 'fullyAutomated', vsan: true },
    { name: 'wld01-cl02', ha: false, drs: 'manual', vsan: true },
  ].map(c => push({
    id: id('cluster', c.name), type: 'cluster', name: c.name, source: src, parent_id: dc.id,
    properties: {
      hostCount: 3, hosts: ['esx01', 'esx02', 'esx03'].map((h, i) => id('host', c.name === 'wld01-cl01' ? h : `esx0${i + 4}`)),
      drsEnabled: true, drsAutomationLevel: c.drs, haEnabled: c.ha, haAdmissionControl: c.ha, evcMode: 'intel-sapphirerapids', vsanEnabled: c.vsan,
      ruleCount: c.name === 'wld01-cl01' ? 2 : 0, totalCpuMhz: 3 * 64 * 2400, totalMemoryBytes: 3 * 1024 * 1024 ** 3, numVms: 15, overallStatus: c.ha ? 'green' : 'yellow',
    },
    relationships: [],
  }))

  const hostSpecs = [
    { n: 'esx01', cl: 0, state: 'connected', cpu: 42, mem: 61 },
    { n: 'esx02', cl: 0, state: 'connected', cpu: 55, mem: 72 },
    { n: 'esx03', cl: 0, state: 'disconnected', cpu: 0, mem: 0 },
    { n: 'esx04', cl: 1, state: 'connected', cpu: 38, mem: 58 },
    { n: 'esx05', cl: 1, state: 'connected', cpu: 71, mem: 84 },
    { n: 'esx06', cl: 1, state: 'connected', cpu: 33, mem: 49 },
  ]
  const hosts = hostSpecs.map((h, hi) => push({
    id: id('host', h.n), type: 'host', name: `${h.n}.wld01.vcf.example`, source: src, parent_id: clusters[h.cl].id,
    properties: {
      connectionState: h.state, powerState: h.state === 'disconnected' ? 'unknown' : 'poweredOn',
      maintenanceMode: false, cluster: clusters[h.cl].name, datacenter: dc.name,
      version: h.n === 'esx06' ? '9.0.0' : '9.0.1', build: h.n === 'esx06' ? '24755230' : '24805960',
      model: 'PowerEdge R760', vendor: 'Dell Inc.', biosVersion: '2.4.4',
      cpuMhz: 2400, numCpuCores: 64, memoryBytes: 1024 * 1024 ** 3,
      uptimeSeconds: h.state === 'disconnected' ? null : 41 * 86400 + 3 * 3600 + 12 * 60,
      bootTime: h.state === 'disconnected' ? null : new Date(now - (41 * 86400 + 3 * 3600 + 12 * 60) * 1000).toISOString(),
      lockdownMode: h.n === 'esx01' ? 'lockdownNormal' : 'lockdownDisabled',
      ntpServers: h.n === 'esx05' ? [] : ['10.16.0.10', '10.16.0.11'], dnsServers: ['10.16.0.2', '10.16.0.3'],
      vmkernelAdapters: [
        { device: 'vmk0', ip: `10.16.11.${20 + hi}`, mtu: 1500, portgroup: 'wld01-mgmt-vlan1611' },
        { device: 'vmk1', ip: `10.16.12.${20 + hi}`, mtu: h.n === 'esx02' ? 9000 : 1500, portgroup: 'wld01-vmotion-vlan1612' },
        { device: 'vmk2', ip: `10.16.13.${20 + hi}`, mtu: 9000, portgroup: 'wld01-vsan-vlan1613' },
        ...(h.n === 'esx02' ? [{ device: 'vmk3', ip: `10.16.14.${20 + hi}`, mtu: 9000, portgroup: 'wld01-tep-vlan1614' }] : []),
      ],
      physicalNics: [
        { device: 'vmnic0', mac: `3c:ec:ef:1a:0${hi}:10`, linkSpeedMb: 25000 },
        { device: 'vmnic1', mac: `3c:ec:ef:1a:0${hi}:11`, linkSpeedMb: 25000 },
      ],
      standardSwitches: [], numVms: h.state === 'disconnected' ? 0 : 5,
      datastores: [h.cl === 0 ? 'wld01-cl01-vsan' : 'wld01-cl02-vsan', 'nfs-backup-01'],
      overallStatus: h.state === 'disconnected' ? 'red' : h.n === 'esx05' ? 'yellow' : 'green',
      cpuUsagePct: h.cpu, memUsagePct: h.mem,
    },
    relationships: [{ kind: 'member_of', target_id: clusters[h.cl].id }],
  }))

  const datastores = [
    { n: 'wld01-cl01-vsan', cl: 0, cap: 61440, free: 5530, type: 'vsan' },
    { n: 'wld01-cl02-vsan', cl: 1, cap: 61440, free: 28100, type: 'vsan' },
    { n: 'nfs-backup-01', cl: null, cap: 20480, free: 11800, type: 'NFS41' },
  ].map(d => push({
    id: id('datastore', d.n), type: 'datastore', name: d.n, source: src, parent_id: dc.id,
    properties: {
      type: d.type, capacity: d.cap * 1024 ** 3, freeSpace: d.free * 1024 ** 3, capacityGB: d.cap, freeGB: d.free, usedPct: Math.round(((d.cap - d.free) / d.cap) * 100),
      accessible: true, maintenanceMode: 'normal', multipleHostAccess: true, vmfsVersion: null,
      url: `ds:///vmfs/volumes/${d.n}/`, hosts: hosts.filter(h => d.cl === null || h.parent_id === clusters[d.cl].id).map(h => h.name), overallStatus: d.free / d.cap < 0.15 ? 'yellow' : 'green',
    },
    relationships: hosts.filter(h => d.cl === null || h.parent_id === clusters[d.cl].id).map(h => ({ kind: 'mounted_on', target_id: h.id })),
  }))

  const networks = [
    { n: 'wld01-mgmt-vlan1611', kind: 'DistributedVirtualPortgroup', vlan: 1611 },
    { n: 'wld01-vmotion-vlan1612', kind: 'DistributedVirtualPortgroup', vlan: 1612 },
    { n: 'wld01-vsan-vlan1613', kind: 'DistributedVirtualPortgroup', vlan: 1613 },
    { n: 'seg-web-tier', kind: 'NsxSegment', vlan: null },
    { n: 'seg-app-tier', kind: 'NsxSegment', vlan: null },
    { n: 'seg-db-tier', kind: 'NsxSegment', vlan: null },
  ].map(n => push({
    id: id('network', n.n), type: 'network', name: n.n, source: src, parent_id: dc.id,
    properties: { type: n.kind === 'NsxSegment' ? 'opaque' : 'dvportgroup', kind: n.kind, vlan: n.vlan, numPorts: n.kind === 'NsxSegment' ? null : 128, switch: n.kind === 'NsxSegment' ? 'wld01-nvds' : 'wld01-vds01', exists: true, transportZone: n.kind === 'NsxSegment' ? 'tz-overlay-01' : null },
    relationships: [],
  }))

  const vmNames = [
    'web01', 'web02', 'web03', 'web04', 'app01', 'app02', 'app03', 'app04', 'db01', 'db02',
    'cache01', 'cache02', 'queue01', 'queue02', 'ci-runner01', 'ci-runner02', 'ci-runner03', 'log01', 'log02', 'metrics01',
    'jump01', 'dns01', 'dns02', 'ldap01', 'gitlab01', 'harbor01', 'vault01', 'backup01', 'test-win11', 'test-rhel9',
  ]
  const vms = vmNames.map((n, i) => {
    const hostIdx = i % 6 === 2 ? (i % 2 === 0 ? 0 : 1) : i % 6 // keep esx03 mostly empty after HA
    const host = hosts[hostIdx]
    const powered = !(n === 'test-win11' || n === 'ci-runner03' || n === 'backup01')
    const seg = n.startsWith('web') ? networks[3] : n.startsWith('app') || n.startsWith('cache') || n.startsWith('queue') ? networks[4] : n.startsWith('db') ? networks[5] : networks[0]
    const ds = host.parent_id === clusters[0].id ? datastores[0] : datastores[1]
    return push({
      id: id('vm', n), type: 'vm', name: n, source: src, parent_id: host.id,
      properties: {
        powerState: powered ? 'poweredOn' : 'poweredOff', connectionState: 'connected',
        host: host.name, cluster: clusters[host.parent_id === clusters[0].id ? 0 : 1].name, resourcePool: 'Resources', folder: n.startsWith('test') ? 'sandbox' : 'workloads',
        guestFullName: n.includes('win') ? 'Microsoft Windows 11 (64-bit)' : n.includes('rhel') ? 'Red Hat Enterprise Linux 9 (64-bit)' : 'Ubuntu Linux (64-bit)',
        guestHostname: powered ? `${n}.wld01.vcf.example` : null, guestIp: powered ? `10.16.${20 + hostIdx}.${10 + i}` : null, guestState: powered ? 'running' : 'notRunning',
        toolsStatus: powered ? (n === 'log02' ? 'toolsNotRunning' : 'toolsOk') : 'toolsNotRunning', toolsVersion: powered && n !== 'log02' ? '12.4.5' : null,
        numCpu: n.startsWith('db') ? 8 : 4, memoryMB: n.startsWith('db') ? 32768 : 8192, hardwareVersion: 'vmx-21', template: false,
        cpuReservationMhz: n.startsWith('db') ? 4000 : 0, memReservationMB: n.startsWith('db') ? 32768 : 0, annotation: n === 'gitlab01' ? 'Snapshots taken before 17.x upgrade' : null,
        snapshotCount: n === 'gitlab01' ? 3 : 0, oldestSnapshotTime: n === 'gitlab01' ? new Date(now - 19 * 86400_000).toISOString() : null,
        disks: [
          { label: 'Hard disk 1', capacityBytes: 64 * 1024 ** 3, datastore: ds.name, thin: true },
          ...(n.startsWith('db') ? [{ label: 'Hard disk 2', capacityBytes: 512 * 1024 ** 3, datastore: ds.name, thin: false }, { label: 'Hard disk 3', capacityBytes: 256 * 1024 ** 3, datastore: ds.name, thin: false }] : []),
        ],
        nics: [{ label: 'Network adapter 1', mac: `00:50:56:9a:${(10 + i).toString(16).padStart(2, '0')}:01`, network: seg.name, connected: powered }],
        networks: [seg.name], datastores: [ds.name],
        storageCommittedBytes: (n.startsWith('db') ? 700 : 38) * 1024 ** 3,
        bootTime: powered ? new Date(now - (7 + i) * 86400_000).toISOString() : null, overallStatus: 'green',
      },
      relationships: [
        { kind: 'runs_on', target_id: host.id },
        { kind: 'stored_on', target_id: ds.id },
        { kind: 'attached_to', target_id: seg.id },
      ],
    })
  })

  const findings: Finding[] = [
    {
      id: 'f-esx03-disconnected', check_id: 'HOST_DISCONNECTED', severity: 'critical',
      title: 'esx03 disconnected from vCenter', summary: 'Host esx03.wld01.vcf.example has been in connectionState disconnected since the last scan. 4 VMs restarted elsewhere by HA.',
      resource_id: hosts[2].id, resource_name: hosts[2].name, resource_type: 'host',
      evidence: { connectionState: { old: 'connected', new: 'disconnected' }, powerState: { old: 'poweredOn', new: 'unknown' }, cluster: 'wld01-cl01', vmsAffected: 4, firstDetected: minutesAgo(2) },
      recommendation: 'Check management network reachability to esx03 (vmk0 on VLAN 1611), confirm the host is powered on via iDRAC, then reconnect from vCenter. Verify vSAN resync after reconnect.',
    },
    {
      id: 'f-vsan01-capacity', check_id: 'DATASTORE_CAPACITY', severity: 'warning',
      title: 'wld01-cl01-vsan is 91% full', summary: 'vSAN datastore wld01-cl01-vsan has 5.4 TB free of 60 TB. Above the 85% warning threshold; vSAN slack space is under 10%.',
      resource_id: datastores[0].id, resource_name: datastores[0].name, resource_type: 'datastore',
      evidence: { usedPct: { old: 84, new: 91 }, freeGB: { old: 9830, new: 5530 }, capacityGB: 61440, threshold: 85 },
      recommendation: 'vSAN needs 25 to 30% slack for rebuilds. The disconnected host esx03 has removed one third of the cluster capacity; reconnecting it restores headroom. Otherwise migrate or delete the largest VMs and old snapshots.',
    },
    {
      id: 'f-cl02-ha-disabled', check_id: 'CLUSTER_HA_DISABLED', severity: 'warning',
      title: 'HA disabled on wld01-cl02', summary: 'vSphere HA is disabled on cluster wld01-cl02. A host failure will not restart its 15 VMs automatically.',
      resource_id: clusters[1].id, resource_name: clusters[1].name, resource_type: 'cluster',
      evidence: { haEnabled: { old: true, new: false }, drsBehavior: 'manual', vmCount: 15 },
      recommendation: 'Re-enable vSphere HA on wld01-cl02 and confirm admission control settings match wld01-cl01.',
    },
    {
      id: 'f-esx05-ntp', check_id: 'HOST_NTP_NOT_CONFIGURED', severity: 'warning',
      title: 'esx05 has no NTP servers configured', summary: 'Host esx05 has an empty NTP server list. Clock drift breaks vSAN, NSX and SSO in unpleasant ways.',
      resource_id: hosts[4].id, resource_name: hosts[4].name, resource_type: 'host',
      evidence: { ntpServers: { old: ['10.16.0.10', '10.16.0.11'], new: [] }, peers: ['esx04', 'esx06'] },
      recommendation: 'Add 10.16.0.10 and 10.16.0.11 as NTP servers on esx05 (matching esx04 and esx06) and start ntpd.',
    },
    {
      id: 'f-gitlab01-snapshots', check_id: 'VM_SNAPSHOT_STALE', severity: 'info',
      title: 'gitlab01 has a 19 day old snapshot', summary: 'VM gitlab01 carries 3 snapshots; the oldest is 19 days. Snapshot chains consume vSAN capacity and slow the VM.',
      resource_id: vms[24].id, resource_name: 'gitlab01', resource_type: 'vm',
      evidence: { snapshotCount: 3, oldestSnapshotTime: new Date(now - 19 * 86400_000).toISOString(), datastore: 'wld01-cl01-vsan' },
      recommendation: 'Consolidate or delete snapshots on gitlab01 after confirming the change window that created them is closed.',
    },
    {
      id: 'f-log02-tools', check_id: 'VM_TOOLS_NOT_RUNNING', severity: 'info',
      title: 'VMware Tools not running on log02', summary: 'log02 is powered on but Tools is not running, so guest IP, heartbeat and graceful shutdown are unavailable.',
      resource_id: vms[18].id, resource_name: 'log02', resource_type: 'vm',
      evidence: { toolsStatus: 'toolsNotRunning', powerState: 'poweredOn' },
      recommendation: 'Start or reinstall open-vm-tools inside log02.',
    },
    {
      id: 'f-cl02-drs-manual', check_id: 'CLUSTER_DRS_DISABLED', severity: 'info',
      title: 'DRS is manual on wld01-cl02', summary: 'DRS automation level on wld01-cl02 is manual, so vCenter will recommend but never perform balancing moves.',
      resource_id: clusters[1].id, resource_name: clusters[1].name, resource_type: 'cluster',
      evidence: { drsEnabled: true, drsAutomationLevel: 'manual' },
      recommendation: 'Set DRS to fully automated unless a workload on this cluster is pinned by design.',
    },
    {
      id: 'f-cl02-version-mismatch', check_id: 'HOST_VERSION_MISMATCH', severity: 'warning',
      title: 'Hosts in wld01-cl02 run different builds', summary: 'esx06 is on 9.0.0 build 24755230 while esx04 and esx05 are on 9.0.1 build 24805960. Mixed builds in one cluster complicate vMotion and vSAN.',
      resource_id: clusters[1].id, resource_name: clusters[1].name, resource_type: 'cluster',
      evidence: { versions: { '9.0.1 (24805960)': ['esx04', 'esx05'], '9.0.0 (24755230)': ['esx06'] } },
      recommendation: 'Remediate esx06 with the cluster image so all three hosts match.',
    },
  ]

  const snapshots: SnapshotSummary[] = ([
    { id: 'snap-vc01-004', created_at: minutesAgo(2), label: 'Current', connection_id: src, scheduled: true, resource_count: r.length, tier: 'recent' },
    { id: 'snap-vc01-003', created_at: minutesAgo(22), label: 'Before firmware window', connection_id: src, scheduled: false, resource_count: r.length, tier: 'manual' },
    { id: 'snap-vc01-002', created_at: minutesAgo(62), label: 'Scheduled', connection_id: src, scheduled: true, resource_count: r.length - 1, tier: 'recent' },
    { id: 'snap-vc01-001', created_at: minutesAgo(180), label: 'Demo baseline', connection_id: src, scheduled: false, resource_count: r.length - 1, tier: 'manual' },
    ...tieredSnapshots(src, 'snap-vc01-h', r.length, 99),
  ] satisfies SnapshotSummary[]).sort((a, b) => b.created_at.localeCompare(a.created_at))

  const changes: Change[] = [
    { change_type: 'modified', resource_id: hosts[2].id, resource_type: 'host', resource_name: hosts[2].name, significance: 'high', summary: 'Host connection state changed', property_changes: { connectionState: { old: 'connected', new: 'disconnected' }, powerState: { old: 'poweredOn', new: 'unknown' } } },
    { change_type: 'modified', resource_id: clusters[1].id, resource_type: 'cluster', resource_name: clusters[1].name, significance: 'high', summary: 'vSphere HA disabled', property_changes: { haEnabled: { old: true, new: false } } },
    {
      change_type: 'modified', resource_id: hosts[1].id, resource_type: 'host', resource_name: hosts[1].name, significance: 'high',
      summary: 'vmkernel vmk1 mtu 1500 -> 9000; vmkernel vmk3 added',
      property_changes: {
        vmkernelAdapters: {
          old: [
            { device: 'vmk0', ip: '10.16.11.21', mtu: 1500, portgroup: 'wld01-mgmt-vlan1611' },
            { device: 'vmk1', ip: '10.16.12.21', mtu: 1500, portgroup: 'wld01-vmotion-vlan1612' },
            { device: 'vmk2', ip: '10.16.13.21', mtu: 9000, portgroup: 'wld01-vsan-vlan1613' },
          ],
          new: [
            { device: 'vmk0', ip: '10.16.11.21', mtu: 1500, portgroup: 'wld01-mgmt-vlan1611' },
            { device: 'vmk1', ip: '10.16.12.21', mtu: 9000, portgroup: 'wld01-vmotion-vlan1612' },
            { device: 'vmk2', ip: '10.16.13.21', mtu: 9000, portgroup: 'wld01-vsan-vlan1613' },
            { device: 'vmk3', ip: '10.16.14.21', mtu: 9000, portgroup: 'wld01-tep-vlan1614' },
          ],
        },
      },
    },
    {
      change_type: 'modified', resource_id: hosts[4].id, resource_type: 'host', resource_name: hosts[4].name, significance: 'medium',
      summary: 'NTP servers 10.16.0.10, 10.16.0.11 removed',
      property_changes: { ntpServers: { old: ['10.16.0.10', '10.16.0.11'], new: [] } },
    },
    {
      change_type: 'modified', resource_id: vms[8].id, resource_type: 'vm', resource_name: 'db01', significance: 'medium',
      summary: 'disk Hard disk 3 added; disk Hard disk 2 thin -> thick',
      property_changes: {
        disks: {
          old: [
            { label: 'Hard disk 1', capacityBytes: 64 * 1024 ** 3, datastore: 'wld01-cl01-vsan', thin: true },
            { label: 'Hard disk 2', capacityBytes: 512 * 1024 ** 3, datastore: 'wld01-cl01-vsan', thin: true },
          ],
          new: [
            { label: 'Hard disk 1', capacityBytes: 64 * 1024 ** 3, datastore: 'wld01-cl01-vsan', thin: true },
            { label: 'Hard disk 2', capacityBytes: 512 * 1024 ** 3, datastore: 'wld01-cl01-vsan', thin: false },
            { label: 'Hard disk 3', capacityBytes: 256 * 1024 ** 3, datastore: 'wld01-cl01-vsan', thin: false },
          ],
        },
        storageCommittedBytes: { old: 300 * 1024 ** 3, new: 700 * 1024 ** 3 },
      },
    },
    { change_type: 'removed', resource_id: id('network', 'seg-legacy-tier'), resource_type: 'network', resource_name: 'seg-legacy-tier', significance: 'high', summary: 'NSX segment removed', property_changes: {} },
    { change_type: 'modified', resource_id: datastores[0].id, resource_type: 'datastore', resource_name: datastores[0].name, significance: 'medium', summary: 'Datastore usage increased', property_changes: { usedPct: { old: 84, new: 91 }, freeGB: { old: 9830, new: 5530 } } },
    { change_type: 'modified', resource_id: vms[0].id, resource_type: 'vm', resource_name: 'web01', significance: 'medium', summary: 'VM moved host; nic Network adapter 1 network seg-legacy-tier -> seg-web-tier', property_changes: { host: { old: 'esx03.wld01.vcf.example', new: 'esx01.wld01.vcf.example' }, networks: { old: ['seg-legacy-tier'], new: ['seg-web-tier'] }, nics: { old: [{ label: 'Network adapter 1', mac: '00:50:56:9a:0a:01', network: 'seg-legacy-tier', connected: true }], new: [{ label: 'Network adapter 1', mac: '00:50:56:9a:0a:01', network: 'seg-web-tier', connected: true }] } } },
    { change_type: 'modified', resource_id: vms[4].id, resource_type: 'vm', resource_name: 'app01', significance: 'medium', summary: 'VM moved host', property_changes: { host: { old: 'esx03.wld01.vcf.example', new: 'esx02.wld01.vcf.example' } } },
    { change_type: 'modified', resource_id: vms[28].id, resource_type: 'vm', resource_name: 'test-win11', significance: 'medium', summary: 'VM powered off', property_changes: { powerState: { old: 'poweredOn', new: 'poweredOff' }, guestIp: { old: '10.16.24.38', new: null } } },
    { change_type: 'added', resource_id: vms[29].id, resource_type: 'vm', resource_name: 'test-rhel9', significance: 'low', summary: 'VM created', property_changes: {} },
    { change_type: 'modified', resource_id: hosts[4].id, resource_type: 'host', resource_name: hosts[4].name, significance: 'low', summary: 'Host resource usage changed', property_changes: { cpuUsagePct: { old: 58, new: 71 }, memUsagePct: { old: 79, new: 84 } } },
    { change_type: 'modified', resource_id: vms[24].id, resource_type: 'vm', resource_name: 'gitlab01', significance: 'low', summary: 'snapshotCount 2 -> 3', property_changes: { snapshotCount: { old: 2, new: 3 } } },
    { change_type: 'modified', resource_id: vms[18].id, resource_type: 'vm', resource_name: 'log02', significance: 'low', summary: 'Tools status changed', property_changes: { toolsStatus: { old: 'toolsOk', new: 'toolsNotRunning' } } },
    { change_type: 'modified', resource_id: vc.id, resource_type: 'vcenter', resource_name: vc.name, significance: 'low', summary: 'vCenter build changed', property_changes: { build: { old: '24755230', new: '24805960' } } },
  ]

  // Persisted change log: the latest window carries the changes above; earlier windows in the
  // last two days carry the smaller things a scheduled scan normally picks up.
  const logRow = (i: number, minAgo: number, fromId: string, toId: string, c: Change): ChangeLogEntry => ({ ...c, id: `chg-${src}-${i}`, observed_at: minutesAgo(minAgo), from_snapshot_id: fromId, to_snapshot_id: toId })
  const mod = (resource: Resource, significance: Change['significance'], summary: string, property_changes: Change['property_changes']): Change => ({ change_type: 'modified', resource_id: resource.id, resource_type: resource.type, resource_name: resource.name, significance, summary, property_changes })
  const changeLog: ChangeLogEntry[] = [
    ...changes.map((c, i) => logRow(i, 2, 'snap-vc01-003', 'snap-vc01-004', c)),
    logRow(20, 62, 'snap-vc01-h099', 'snap-vc01-002', mod(vms[5], 'medium', 'VM moved host', { host: { old: 'esx01.wld01.vcf.example', new: 'esx02.wld01.vcf.example' } })),
    logRow(21, 62, 'snap-vc01-h099', 'snap-vc01-002', mod(vms[12], 'low', 'Host resource usage changed', { cpuUsagePct: { old: 12, new: 19 } })),
    logRow(22, 142, 'snap-vc01-h098', 'snap-vc01-h099', mod(vms[2], 'medium', 'VM powered off', { powerState: { old: 'poweredOn', new: 'poweredOff' }, guestIp: { old: '10.16.22.12', new: null } })),
    logRow(23, 222, 'snap-vc01-h096', 'snap-vc01-h097', mod(hosts[3], 'high', 'rebooted', { bootTime: { old: new Date(now - 48 * 86400_000).toISOString(), new: new Date(now - 4 * 3600_000).toISOString() }, uptimeSeconds: { old: 48 * 86400, new: 4 * 3600 } })),
    logRow(24, 262, 'snap-vc01-h095', 'snap-vc01-h096', mod(hosts[3], 'medium', 'Host entered maintenance mode', { maintenanceMode: { old: false, new: true } })),
    logRow(25, 342, 'snap-vc01-h093', 'snap-vc01-h094', mod(networks[1], 'high', 'VLAN 1612 -> 1622', { vlan: { old: 1612, new: 1622 } })),
    logRow(26, 422, 'snap-vc01-h091', 'snap-vc01-h092', mod(vms[4], 'medium', 'memoryMB 8192 -> 16384; numCpu 4 -> 8', { memoryMB: { old: 8192, new: 16384 }, numCpu: { old: 4, new: 8 } })),
    logRow(27, 542, 'snap-vc01-h088', 'snap-vc01-h089', mod(hosts[4], 'medium', 'NTP servers changed', { ntpServers: { old: ['10.16.0.10'], new: ['10.16.0.10', '10.16.0.11'] } })),
    logRow(28, 702, 'snap-vc01-h084', 'snap-vc01-h085', mod(datastores[1], 'low', 'Datastore usage changed', { usedPct: { old: 52, new: 54 }, freeGB: { old: 29500, new: 28100 } })),
    logRow(29, 900, 'snap-vc01-h082', 'snap-vc01-h083', { change_type: 'added', resource_id: vms[15].id, resource_type: 'vm', resource_name: 'ci-runner02', significance: 'low', summary: 'VM created', property_changes: {} }),
    logRow(30, 1260, 'snap-vc01-h078', 'snap-vc01-h079', mod(vms[24], 'low', 'snapshotCount 1 -> 2', { snapshotCount: { old: 1, new: 2 } })),
    logRow(31, 1620, 'snap-vc01-h076', 'snap-vc01-h077', mod(clusters[0], 'medium', 'DRS automation level changed', { drsAutomationLevel: { old: 'partiallyAutomated', new: 'fullyAutomated' } })),
    logRow(32, 2160, 'snap-vc01-h073', 'snap-vc01-h074', mod(hosts[5], 'high', 'Host connection state changed', { connectionState: { old: 'notResponding', new: 'connected' } })),
    logRow(33, 2340, 'snap-vc01-h072', 'snap-vc01-h073', mod(hosts[5], 'high', 'Host connection state changed', { connectionState: { old: 'connected', new: 'notResponding' } })),
  ].sort((a, b) => b.observed_at.localeCompare(a.observed_at))

  // What vCenter recorded around those changes. Times are relative to scan time; the latest
  // window (2 to 22 minutes ago) explains the esx03 disconnect and what happened around it.
  let evSeq = 1
  const ev = (minAgo: number, source: EventSource, type: string, category: EventCategory, message: string, user: string | null, res: Resource | null): Event => ({
    id: `${src}:${evSeq++}`, connection_id: src, time: minutesAgo(minAgo), source, type, category, message, user,
    resource_id: res?.id ?? null, resource_name: res?.name ?? null, resource_type: res?.type ?? null,
  })
  const admin = 'VSPHERE.LOCAL\\Administrator'
  const ops = 'WLD01\\ops-jsmith'
  const events: Event[] = [
    ev(4, 'event', 'com.vmware.vc.ha.VmRestartedByHAEvent', 'warning', 'vSphere HA restarted virtual machine app01 on host esx02.wld01.vcf.example in cluster wld01-cl01', null, vms[4]),
    ev(4, 'event', 'com.vmware.vc.ha.VmRestartedByHAEvent', 'warning', 'vSphere HA restarted virtual machine web01 on host esx01.wld01.vcf.example in cluster wld01-cl01', null, vms[0]),
    ev(6, 'event', 'AlarmStatusChangedEvent', 'error', "Alarm 'Host connection and power state' on esx03.wld01.vcf.example changed from Green to Red", null, hosts[2]),
    ev(6, 'event', 'HostConnectionLostEvent', 'error', 'Host esx03.wld01.vcf.example in wld01-dc is not responding', null, hosts[2]),
    ev(7, 'event', 'com.vmware.vc.HA.HostStateChangedEvent', 'warning', 'The vSphere HA availability state of the host esx03.wld01.vcf.example has changed to Dead', null, hosts[2]),
    ev(9, 'task', 'Task: Remove segment', 'user', 'Remove segment seg-legacy-tier from transport zone tz-overlay-01', admin, null),
    ev(11, 'task', 'Task: Update network configuration', 'user', 'Update network configuration on esx02.wld01.vcf.example: vmk1 MTU 9000, add vmk3 on wld01-tep-vlan1614', ops, hosts[1]),
    ev(12, 'event', 'HostVnicConnectedToCustomizedDVPortEvent', 'info', 'vmk3 on host esx02.wld01.vcf.example connected to port 41 on wld01-vds01', ops, hosts[1]),
    ev(14, 'event', 'DasDisabledEvent', 'user', 'vSphere HA disabled for cluster wld01-cl02 in wld01-dc', admin, clusters[1]),
    ev(15, 'task', 'Task: Reconfigure cluster', 'user', 'Reconfigure cluster wld01-cl02: HA disabled, admission control off', admin, clusters[1]),
    ev(17, 'event', 'VmReconfiguredEvent', 'user', 'Reconfigured db01 on esx05.wld01.vcf.example in wld01-dc. Added Hard disk 3 (256 GB, thick); Hard disk 2 changed to thick provisioned', ops, vms[8]),
    ev(19, 'event', 'HostSyncFailedEvent', 'warning', 'Failed to sync with the vCenter Agent on host esx03.wld01.vcf.example', null, hosts[2]),
    ev(20, 'event', 'UserLoginSessionEvent', 'info', `User ${admin}@10.16.0.51 logged in as VMware vim-java 1.0`, admin, null),
    ev(21, 'event', 'com.vmware.vc.vsan.HostVsanNetworkMismatch', 'warning', 'vSAN network configuration on esx05.wld01.vcf.example is not in sync with cluster wld01-cl02 (NTP servers removed)', null, hosts[4]),
    ev(28, 'task', 'Task: Create virtual machine snapshot', 'user', 'Create virtual machine snapshot "pre-17.11 upgrade" on gitlab01', ops, vms[24]),
    ev(31, 'event', 'VmPoweredOffEvent', 'user', 'test-win11 on esx03.wld01.vcf.example in wld01-dc is powered off', ops, vms[28]),
    ev(34, 'event', 'VmCreatedEvent', 'user', 'Created virtual machine test-rhel9 on esx04.wld01.vcf.example in wld01-dc', ops, vms[29]),
    ev(58, 'event', 'DrsVmMigratedEvent', 'info', 'DRS migrated app02 from esx01.wld01.vcf.example to esx02.wld01.vcf.example in cluster wld01-cl01', null, vms[5]),
    ev(65, 'event', 'ScheduledTaskCompletedEvent', 'info', "Scheduled task 'Weekly vSAN health check' completed on wld01-cl01", null, clusters[0]),
    ev(140, 'event', 'VmPoweredOffEvent', 'user', 'web03 on esx01.wld01.vcf.example in wld01-dc is powered off', admin, vms[2]),
    ev(141, 'event', 'VmGuestShutdownEvent', 'user', 'Guest OS shut down for web03 on esx01.wld01.vcf.example in wld01-dc', admin, vms[2]),
    ev(218, 'event', 'HostConnectedEvent', 'info', 'Connected to esx04.wld01.vcf.example in wld01-dc', null, hosts[3]),
    ev(221, 'event', 'ExitMaintenanceModeEvent', 'user', 'esx04.wld01.vcf.example in wld01-dc has exited maintenance mode', ops, hosts[3]),
    ev(236, 'event', 'HostShutdownEvent', 'user', 'Host esx04.wld01.vcf.example in wld01-dc was rebooted by ops-jsmith: BIOS 2.4.4 firmware update', ops, hosts[3]),
    ev(258, 'event', 'EnteredMaintenanceModeEvent', 'user', 'esx04.wld01.vcf.example in wld01-dc has entered maintenance mode', ops, hosts[3]),
    ev(259, 'task', 'Task: Enter maintenance mode', 'user', 'Enter maintenance mode on esx04.wld01.vcf.example (evacuate powered off VMs, ensure accessibility)', ops, hosts[3]),
    ev(261, 'event', 'VmMigratedEvent', 'user', 'Migration of app02 from esx04.wld01.vcf.example to esx05.wld01.vcf.example completed', ops, vms[5]),
    ev(338, 'task', 'Task: Reconfigure distributed port group', 'user', 'Reconfigure dvPort group wld01-vmotion-vlan1612 on wld01-vds01: VLAN 1612 -> 1622', admin, networks[1]),
    ev(339, 'event', 'DVPortgroupReconfiguredEvent', 'user', 'dvPort group wld01-vmotion-vlan1612 in wld01-dc was reconfigured', admin, networks[1]),
    ev(420, 'event', 'VmReconfiguredEvent', 'user', 'Reconfigured app01 on esx02.wld01.vcf.example in wld01-dc. Memory 8 GB -> 16 GB, CPUs 4 -> 8', ops, vms[4]),
    ev(540, 'task', 'Task: Update date/time configuration', 'user', 'Update date/time configuration on esx05.wld01.vcf.example: NTP servers 10.16.0.10, 10.16.0.11', ops, hosts[4]),
    ev(612, 'event', 'AlarmStatusChangedEvent', 'warning', "Alarm 'Datastore usage on disk' on wld01-cl01-vsan changed from Green to Yellow", null, datastores[0]),
    ev(700, 'event', 'com.vmware.vc.vsan.ResyncStartedEvent', 'info', 'vSAN resync started on wld01-cl01-vsan: 1.2 TB to resync', null, datastores[0]),
    ev(1080, 'event', 'UserLogoutSessionEvent', 'info', `User ${ops}@10.16.0.77 logged out (login time: yesterday, number of API invocations: 412)`, ops, null),
    ev(1258, 'task', 'Task: Create virtual machine snapshot', 'user', 'Create virtual machine snapshot "pre-17.10 upgrade" on gitlab01', ops, vms[24]),
    ev(1620, 'event', 'ClusterReconfiguredEvent', 'user', 'Reconfigured cluster wld01-cl01 in datacenter wld01-dc: DRS automation level fullyAutomated', admin, clusters[0]),
    ev(2160, 'event', 'HostConnectedEvent', 'info', 'Connected to esx06.wld01.vcf.example in wld01-dc', null, hosts[5]),
    ev(2340, 'event', 'HostConnectionLostEvent', 'error', 'Host esx06.wld01.vcf.example in wld01-dc is not responding', null, hosts[5]),
    ev(2341, 'event', 'AlarmStatusChangedEvent', 'error', "Alarm 'Host connection and power state' on esx06.wld01.vcf.example changed from Green to Red", null, hosts[5]),
    ev(4300, 'event', 'GeneralUserEvent', 'user', 'Cluster image remediation scheduled for wld01-cl02 (esx06 pending)', admin, clusters[1]),
    ev(7200, 'event', 'LicenseExpiredEvent', 'warning', 'Evaluation license for wld01 NSX will expire in 30 days', null, null),
  ]

  const scans: ScanRun[] = [
    { id: 'scan-vc01-9', connection_id: src, started: minutesAgo(2), finished: minutesAgo(1.7), status: 'ok', error: null, snapshot_id: 'snap-vc01-004', trigger: 'scheduled' },
    { id: 'scan-vc01-8', connection_id: src, started: minutesAgo(22), finished: minutesAgo(21.5), status: 'ok', error: null, snapshot_id: 'snap-vc01-003', trigger: 'manual' },
    { id: 'scan-vc01-7', connection_id: src, started: minutesAgo(42), finished: minutesAgo(41), status: 'error', error: 'timeout after 60s connecting to vc-wld01.wld01.vcf.example:443', snapshot_id: null, trigger: 'scheduled' },
  ]

  return {
    connection: { id: src, name: 'Centennial Lab (wld01)', host: 'vc-wld01.wld01.vcf.example', username: 'administrator@vsphere.local', verify_tls: false, created_at: minutesAgo(400), kind: 'vcenter' },
    schedule: { connection_id: src, interval_minutes: 5, enabled: true, last_run: minutesAgo(2), next_run: new Date(now + 3 * 60_000).toISOString(), last_status: 'ok' },
    resources: r, findings, snapshots, changes, changeLog, events, scans,
  }
}

function buildManagementDomain(): MockEstate {
  const src = 'vc00'
  const id = (type: string, name: string) => `${type}:${src}:${name}`
  const r: Resource[] = []
  const vc: Resource = { id: id('vcenter', 'mgmt-vc'), type: 'vcenter', name: 'vc-mgmt.mgmt.vcf.example', source: src, parent_id: null, properties: { version: '9.0.1', build: '24805960' }, relationships: [] }
  const dc: Resource = { id: id('datacenter', 'mgmt-dc'), type: 'datacenter', name: 'mgmt-dc', source: src, parent_id: vc.id, properties: {}, relationships: [] }
  const cl: Resource = { id: id('cluster', 'mgmt-cl01'), type: 'cluster', name: 'mgmt-cl01', source: src, parent_id: dc.id, properties: { haEnabled: true, drsEnabled: true, vsanEnabled: true, numHosts: 4 }, relationships: [] }
  r.push(vc, dc, cl)
  const hosts = ['mgmt-esx01', 'mgmt-esx02', 'mgmt-esx03', 'mgmt-esx04'].map((n, i) => {
    const h: Resource = { id: id('host', n), type: 'host', name: `${n}.wld01.vcf.example`, source: src, parent_id: cl.id, properties: { connectionState: 'connected', powerState: 'poweredOn', maintenanceMode: false, version: '9.0.1', build: '24805960', model: 'PowerEdge R760', vendor: 'Dell Inc.', numCpuCores: 64, memoryBytes: 768 * 1024 ** 3, uptimeSeconds: 120 * 86400 + i * 3600, lockdownMode: 'lockdownNormal', ntpServers: ['10.16.0.10', '10.16.0.11'], dnsServers: ['10.16.0.2'], vmkernelAdapters: [{ device: 'vmk0', ip: `10.16.1.${20 + i}`, mtu: 1500, portgroup: 'mgmt-vlan1600' }], physicalNics: [{ device: 'vmnic0', mac: `3c:ec:ef:2b:00:1${i}`, linkSpeedMb: 25000 }], cpuUsagePct: 30 + i * 5, memUsagePct: 50 + i * 4 }, relationships: [] }
    r.push(h); return h
  })
  const ds: Resource = { id: id('datastore', 'mgmt-vsan'), type: 'datastore', name: 'mgmt-vsan', source: src, parent_id: dc.id, properties: { type: 'vsan', capacityGB: 40960, freeGB: 22000, usedPct: 46, accessible: true }, relationships: [] }
  const net: Resource = { id: id('network', 'mgmt-vlan1600'), type: 'network', name: 'mgmt-vlan1600', source: src, parent_id: dc.id, properties: { kind: 'DistributedVirtualPortgroup', vlanId: 1600 }, relationships: [] }
  r.push(ds, net)
  const vmNames = ['sddc-manager', 'nsx-mgr-01', 'nsx-mgr-02', 'nsx-mgr-03', 'vc01', 'mgmt-vc', 'ops-01', 'aria-lcm']
  vmNames.forEach((n, i) => r.push({ id: id('vm', n), type: 'vm', name: n, source: src, parent_id: hosts[i % 4].id, properties: { powerState: 'poweredOn', host: hosts[i % 4].name, numCpu: 8, memoryMB: 49152, hardwareVersion: 'vmx-21', toolsStatus: 'toolsOk', guestFullName: 'VMware Photon OS (64-bit)', snapshotCount: 0, disks: [{ label: 'Hard disk 1', capacityBytes: 200 * 1024 ** 3, datastore: ds.name, thin: true }], nics: [{ label: 'Network adapter 1', mac: `00:50:56:8b:00:0${i}`, network: net.name, connected: true }], datastores: [ds.name], networks: [net.name] }, relationships: [{ kind: 'runs_on', target_id: hosts[i % 4].id }] }))
  return {
    connection: { id: src, name: 'Management domain', host: 'vc-mgmt.mgmt.vcf.example', username: 'administrator@vsphere.local', verify_tls: true, created_at: minutesAgo(900), kind: 'vcenter' },
    schedule: { connection_id: src, interval_minutes: 15, enabled: true, last_run: minutesAgo(9), next_run: new Date(now + 6 * 60_000).toISOString(), last_status: 'ok' },
    resources: r,
    findings: [],
    snapshots: [
      { id: 'snap-vc00-002', created_at: minutesAgo(9), label: 'Scheduled', connection_id: src, scheduled: true, resource_count: r.length, tier: 'recent' },
      { id: 'snap-vc00-001', created_at: minutesAgo(130), label: 'Scheduled', connection_id: src, scheduled: true, resource_count: r.length, tier: 'recent' },
      { id: 'snap-vc00-000', created_at: hoursAgo(26), label: 'Scheduled', connection_id: src, scheduled: true, resource_count: r.length, tier: 'hourly' },
      { id: 'snap-vc00-d01', created_at: hoursAgo(74), label: 'Scheduled', connection_id: src, scheduled: true, resource_count: r.length, tier: 'daily' },
    ],
    changes: [
      { change_type: 'modified', resource_id: hosts[1].id, resource_type: 'host', resource_name: hosts[1].name, significance: 'low', summary: 'Host resource usage changed', property_changes: { cpuUsagePct: { old: 31, new: 35 } } },
    ],
    changeLog: [
      { id: 'chg-vc00-1', observed_at: minutesAgo(9), from_snapshot_id: 'snap-vc00-001', to_snapshot_id: 'snap-vc00-002', change_type: 'modified', resource_id: hosts[1].id, resource_type: 'host', resource_name: hosts[1].name, significance: 'low', summary: 'Host resource usage changed', property_changes: { cpuUsagePct: { old: 31, new: 35 } } },
    ],
    events: [
      { id: `${src}:1`, connection_id: src, time: minutesAgo(12), source: 'event', category: 'info', type: 'UserLoginSessionEvent', message: 'User VSPHERE.LOCAL\\svc-vcfdoctor@10.16.0.90 logged in as pyvmomi', user: 'VSPHERE.LOCAL\\svc-vcfdoctor', resource_id: null, resource_name: null, resource_type: null },
      { id: `${src}:2`, connection_id: src, time: minutesAgo(95), source: 'task', category: 'user', type: 'Task: Create virtual machine snapshot', message: 'Create virtual machine snapshot "pre-patch" on sddc-manager', user: 'VSPHERE.LOCAL\\Administrator', resource_id: id('vm', 'sddc-manager'), resource_name: 'sddc-manager', resource_type: 'vm' },
      { id: `${src}:3`, connection_id: src, time: minutesAgo(640), source: 'event', category: 'warning', type: 'AlarmStatusChangedEvent', message: "Alarm 'Virtual machine memory usage' on nsx-mgr-02 changed from Green to Yellow", user: null, resource_id: id('vm', 'nsx-mgr-02'), resource_name: 'nsx-mgr-02', resource_type: 'vm' },
    ],
    scans: [
      { id: 'scan-vc00-3', connection_id: src, started: minutesAgo(9), finished: minutesAgo(8.6), status: 'ok', error: null, snapshot_id: 'snap-vc00-002', trigger: 'scheduled' },
    ],
  }
}

export const mockState = {
  estates: [buildWorkloadDomain(), buildManagementDomain()] as MockEstate[],
  settings: { retention_policy: { recent_days: 14, hourly_days: 30, daily_days: 365 }, changes_min_significance: 'low', assistant: { enabled: true, provider: 'mock', model: 'claude-opus-5', api_key_set: false } } as Settings,
  assistantStatus: { available: true, provider: 'mock', model: 'claude-opus-5', reason: null } as AssistantStatus,
  nextId: 100,
}

export function mockEstate(connectionId?: string | null): MockEstate[] {
  if (!connectionId) return mockState.estates
  return mockState.estates.filter(e => e.connection.id === connectionId)
}

export function delay<T>(v: T, ms = 250): Promise<T> {
  return new Promise(res => setTimeout(() => res(v), ms))
}

export function mockAssistantText(task: string, format: string | undefined, findingTitle: string | undefined): string {
  const subject = findingTitle ?? 'the selected evidence'
  if (task === 'generate-script') {
    const fmt = format ?? 'powercli'
    const code: Record<string, { read: string; modify: string; lang: string }> = {
      powercli: {
        lang: 'powershell',
        read: `Connect-VIServer vc-wld01.wld01.vcf.example\n$h = Get-VMHost esx03.wld01.vcf.example\n$h | Select Name, ConnectionState, PowerState, Version, Build\nGet-VM -Location $h | Select Name, PowerState, VMHost\nGet-Datastore wld01-cl01-vsan | Select Name, CapacityGB, FreeSpaceGB`,
        modify: `# Reconnect the host after confirming management network reachability\n$h = Get-VMHost esx03.wld01.vcf.example\nSet-VMHost -VMHost $h -State Connected -Confirm:$true`,
      },
      python: {
        lang: 'python',
        read: `from pyVim.connect import SmartConnect\nfrom pyVmomi import vim\n\nsi = SmartConnect(host="vc-wld01.wld01.vcf.example", user=USER, pwd=PWD, disableSslCertValidation=True)\ncontent = si.RetrieveContent()\nview = content.viewManager.CreateContainerView(content.rootFolder, [vim.HostSystem], True)\nfor host in view.view:\n    print(host.name, host.runtime.connectionState, host.runtime.powerState)`,
        modify: `# Reconnect esx03 once reachable\nhost = next(h for h in view.view if h.name.startswith("esx03"))\ntask = host.ReconnectHost_Task()\n# wait for task and inspect task.info.state`,
      },
      shell: {
        lang: 'bash',
        read: `# From a jump host on the management VLAN\nping -c 3 esx03.wld01.vcf.example\nnc -zv esx03.wld01.vcf.example 443 902\n# On the host console (DCUI or SSH if enabled)\nesxcli network ip interface ipv4 get\nesxcli vsan cluster get\nvim-cmd hostsvc/hostsummary | grep -E "connectionState|powerState"`,
        modify: `# Restart management agents on esx03 (brief loss of vCenter connectivity to this host)\n/etc/init.d/hostd restart\n/etc/init.d/vpxa restart`,
      },
      rest: {
        lang: 'bash',
        read: `TOKEN=$(curl -sk -u "$USER:$PWD" -X POST https://vc-wld01.wld01.vcf.example/api/session | tr -d '"')\ncurl -sk -H "vmware-api-session-id: $TOKEN" https://vc-wld01.wld01.vcf.example/api/vcenter/host | jq .\ncurl -sk -H "vmware-api-session-id: $TOKEN" "https://vc-wld01.wld01.vcf.example/api/vcenter/datastore?names=wld01-cl01-vsan" | jq .`,
        modify: `# Reconnect via the host's connect action (vSphere Automation API)\ncurl -sk -H "vmware-api-session-id: $TOKEN" -X POST \\\n  "https://vc-wld01.wld01.vcf.example/api/vcenter/host/host-1012?action=connect"`,
      },
    }
    const c = code[fmt] ?? code.powercli
    return `# ${fmt.toUpperCase()} investigation script for ${subject}\n\nThe script is split into a read-only section you can run immediately and a modifying section that requires review and a change window.\n\n## READ ONLY: gather host, VM and datastore state\n\n\`\`\`${c.lang}\n${c.read}\n\`\`\`\n\nReview the output. Expected: esx03 shows \`disconnected\`, its former VMs now report a different host, and wld01-cl01-vsan is above 85% used.\n\n## MODIFIES ENVIRONMENT: reconnect the host\n\nRun only after iDRAC shows the host powered on and vmk0 answers on VLAN 1611.\n\n\`\`\`${c.lang}\n${c.modify}\n\`\`\`\n\nAfter reconnecting, watch vSAN resync before declaring the datastore healthy.`
  }
  if (task === 'investigate') {
    return `## Investigation plan for ${subject}\n\n1. **Confirm the symptom.** connectionState moved from \`connected\` to \`disconnected\` at the last scan, and 4 VMs were restarted on esx01 and esx02 by HA.\n2. **Check what changed around it.** Three high impact changes landed in the same window: this disconnect, HA being disabled on wld01-cl02, and the removal of NSX segment seg-legacy-tier. The segment removal is the only one that touched the network layer.\n3. **Test management reachability.** vmk0 on esx03 lives on VLAN 1611. Ping and TCP 443 and 902 from a management VLAN jump host.\n4. **Check the physical host.** iDRAC power state and the last system event log entries.\n5. **Check capacity impact.** wld01-cl01-vsan moved from 84% to 91% used because one third of the cluster's capacity left with esx03.\n\nMost likely cause given the evidence: a management network or host outage, not a vCenter problem, because the other five hosts stayed connected through the same scan.`
  }
  if (task === 'explain') {
    return `## What this finding means\n\n**${subject}.** vCenter has lost its management connection to the host. The host may still be running workloads, but vCenter can no longer see or control it.\n\n## Evidence used\n\n- connectionState: \`connected\` to \`disconnected\`\n- powerState: \`poweredOn\` to \`unknown\`\n- 4 VMs moved off esx03 to esx01 and esx02 (HA restart)\n- wld01-cl01-vsan usage rose from 84% to 91% in the same window\n\n## Why it matters\n\nwld01-cl01 is a three node vSAN cluster. With one host out, the cluster has no tolerance for a second failure and vSAN cannot rebuild the missing components. The capacity warning on the datastore is a direct consequence of this finding, not a separate problem.\n\n## Recommended next step\n\nConfirm management network reachability to esx03, then reconnect it from vCenter. Use **Investigate** for the ordered plan or **Generate Script** for read-only checks.`
  }
  return `I can answer using the evidence currently in view: findings, recent changes and the related resources. Ask about a specific finding or pick one on the Health page and use Explain.\n\nRight now the estate has 1 critical finding (esx03 disconnected), 3 warnings and 12 recent changes. The most important problem is the disconnected host, which also explains the vSAN capacity warning on wld01-cl01-vsan.`
}
