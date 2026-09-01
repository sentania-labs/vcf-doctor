// Realistic mock estate: a VCF workload domain with two clusters, six hosts, thirty VMs,
// vSAN datastores and NSX segments, one disconnected host, one datastore at 91%.
import type {
  Change, ConnectionPublic, Finding, Resource, ScanRun, Schedule, SnapshotSummary, Settings, AssistantStatus,
} from '@/types'

const now = Date.now()
const minutesAgo = (m: number) => new Date(now - m * 60_000).toISOString()

export interface MockEstate {
  connection: ConnectionPublic
  schedule: Schedule
  resources: Resource[]
  findings: Finding[]
  snapshots: SnapshotSummary[]
  changes: Change[]
  scans: ScanRun[]
}

function buildWorkloadDomain(): MockEstate {
  const src = 'vc01'
  const id = (type: string, name: string) => `${type}:${src}:${name}`
  const r: Resource[] = []
  const push = (res: Resource) => { r.push(res); return res }

  const vc = push({
    id: id('vcenter', 'vc01'), type: 'vcenter', name: 'vc-wld01.wld01.vcf.example', source: src, parent_id: null,
    properties: { version: '9.0.1', build: '24805960', apiType: 'VirtualCenter', instanceUuid: 'c9b2c5d0-2f0e-4a4e-9f8f-1a2b3c4d5e6f' },
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
    properties: { haEnabled: c.ha, drsEnabled: true, drsBehavior: c.drs, vsanEnabled: c.vsan, numHosts: 3, evcMode: 'intel-sapphirerapids' },
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
  const hosts = hostSpecs.map(h => push({
    id: id('host', h.n), type: 'host', name: `${h.n}.wld01.vcf.example`, source: src, parent_id: clusters[h.cl].id,
    properties: {
      connectionState: h.state, powerState: h.state === 'disconnected' ? 'unknown' : 'poweredOn',
      maintenanceMode: false, version: '9.0.1', build: '24805960', model: 'Dell PowerEdge R760',
      cpuCores: 64, memoryGB: 1024, cpuUsagePct: h.cpu, memUsagePct: h.mem, uptimeDays: h.state === 'disconnected' ? 0 : 41,
      ntpSynced: h.n !== 'esx05', vmkernelNics: ['vmk0', 'vmk1', 'vmk2', 'vmk10'],
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
      type: d.type, capacityGB: d.cap, freeGB: d.free, usedPct: Math.round(((d.cap - d.free) / d.cap) * 100),
      accessible: true, maintenanceMode: 'normal', hostCount: d.cl === null ? 6 : 3,
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
    properties: { kind: n.kind, vlanId: n.vlan, switch: n.kind === 'NsxSegment' ? 'wld01-nvds' : 'wld01-vds01', accessible: true, transportZone: n.kind === 'NsxSegment' ? 'tz-overlay-01' : null },
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
        powerState: powered ? 'poweredOn' : 'poweredOff', guestOS: n.includes('win') ? 'Windows 11 (64-bit)' : 'Ubuntu Linux (64-bit)',
        vcpus: n.startsWith('db') ? 8 : 4, memoryGB: n.startsWith('db') ? 32 : 8, toolsStatus: powered ? (n === 'log02' ? 'toolsNotRunning' : 'toolsOk') : 'toolsNotRunning',
        snapshots: n === 'gitlab01' ? 3 : 0, oldestSnapshotDays: n === 'gitlab01' ? 19 : null, host: host.name, datastore: ds.name, network: seg.name,
        ipAddress: powered ? `10.16.${20 + hostIdx}.${10 + i}` : null,
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
      id: 'f-esx03-disconnected', check_id: 'host_disconnected', severity: 'critical',
      title: 'esx03 disconnected from vCenter', summary: 'Host esx03.wld01.vcf.example has been in connectionState disconnected since the last scan. 4 VMs restarted elsewhere by HA.',
      resource_id: hosts[2].id, resource_name: hosts[2].name, resource_type: 'host',
      evidence: { connectionState: { old: 'connected', new: 'disconnected' }, powerState: { old: 'poweredOn', new: 'unknown' }, cluster: 'wld01-cl01', vmsAffected: 4, firstDetected: minutesAgo(2) },
      recommendation: 'Check management network reachability to esx03 (vmk0 on VLAN 1611), confirm the host is powered on via iDRAC, then reconnect from vCenter. Verify vSAN resync after reconnect.',
    },
    {
      id: 'f-vsan01-capacity', check_id: 'datastore_capacity', severity: 'warning',
      title: 'wld01-cl01-vsan is 91% full', summary: 'vSAN datastore wld01-cl01-vsan has 5.4 TB free of 60 TB. Above the 85% warning threshold; vSAN slack space is under 10%.',
      resource_id: datastores[0].id, resource_name: datastores[0].name, resource_type: 'datastore',
      evidence: { usedPct: { old: 84, new: 91 }, freeGB: { old: 9830, new: 5530 }, capacityGB: 61440, threshold: 85 },
      recommendation: 'vSAN needs 25 to 30% slack for rebuilds. The disconnected host esx03 has removed one third of the cluster capacity; reconnecting it restores headroom. Otherwise migrate or delete the largest VMs and old snapshots.',
    },
    {
      id: 'f-cl02-ha-disabled', check_id: 'cluster_ha_disabled', severity: 'warning',
      title: 'HA disabled on wld01-cl02', summary: 'vSphere HA is disabled on cluster wld01-cl02. A host failure will not restart its 15 VMs automatically.',
      resource_id: clusters[1].id, resource_name: clusters[1].name, resource_type: 'cluster',
      evidence: { haEnabled: { old: true, new: false }, drsBehavior: 'manual', vmCount: 15 },
      recommendation: 'Re-enable vSphere HA on wld01-cl02 and confirm admission control settings match wld01-cl01.',
    },
    {
      id: 'f-esx05-ntp', check_id: 'host_ntp', severity: 'warning',
      title: 'esx05 NTP not synchronized', summary: 'Host esx05 reports NTP not synced. Clock drift breaks vSAN, NSX and SSO in unpleasant ways.',
      resource_id: hosts[4].id, resource_name: hosts[4].name, resource_type: 'host',
      evidence: { ntpSynced: false, ntpServers: ['10.16.0.10', '10.16.0.11'], driftSeconds: 7.4 },
      recommendation: 'Verify UDP 123 from esx05 to the NTP servers and restart ntpd on the host.',
    },
    {
      id: 'f-gitlab01-snapshots', check_id: 'vm_old_snapshots', severity: 'info',
      title: 'gitlab01 has a 19 day old snapshot', summary: 'VM gitlab01 carries 3 snapshots; the oldest is 19 days. Snapshot chains consume vSAN capacity and slow the VM.',
      resource_id: vms[24].id, resource_name: 'gitlab01', resource_type: 'vm',
      evidence: { snapshots: 3, oldestSnapshotDays: 19, datastore: 'wld01-cl01-vsan' },
      recommendation: 'Consolidate or delete snapshots on gitlab01 after confirming the change window that created them is closed.',
    },
    {
      id: 'f-log02-tools', check_id: 'vm_tools_not_running', severity: 'info',
      title: 'VMware Tools not running on log02', summary: 'log02 is powered on but Tools is not running, so guest IP, heartbeat and graceful shutdown are unavailable.',
      resource_id: vms[18].id, resource_name: 'log02', resource_type: 'vm',
      evidence: { toolsStatus: 'toolsNotRunning', powerState: 'poweredOn' },
      recommendation: 'Start or reinstall open-vm-tools inside log02.',
    },
  ]

  const snapshots: SnapshotSummary[] = [
    { id: 'snap-vc01-004', created_at: minutesAgo(2), label: 'Current', connection_id: src, scheduled: true, resource_count: r.length },
    { id: 'snap-vc01-003', created_at: minutesAgo(22), label: 'Before firmware window', connection_id: src, scheduled: false, resource_count: r.length },
    { id: 'snap-vc01-002', created_at: minutesAgo(62), label: 'Scheduled', connection_id: src, scheduled: true, resource_count: r.length - 1 },
    { id: 'snap-vc01-001', created_at: minutesAgo(180), label: 'Demo baseline', connection_id: src, scheduled: false, resource_count: r.length - 1 },
  ]

  const changes: Change[] = [
    { change_type: 'modified', resource_id: hosts[2].id, resource_type: 'host', resource_name: hosts[2].name, significance: 'high', summary: 'Host connection state changed', property_changes: { connectionState: { old: 'connected', new: 'disconnected' }, powerState: { old: 'poweredOn', new: 'unknown' } } },
    { change_type: 'modified', resource_id: clusters[1].id, resource_type: 'cluster', resource_name: clusters[1].name, significance: 'high', summary: 'vSphere HA disabled', property_changes: { haEnabled: { old: true, new: false } } },
    { change_type: 'removed', resource_id: id('network', 'seg-legacy-tier'), resource_type: 'network', resource_name: 'seg-legacy-tier', significance: 'high', summary: 'NSX segment removed', property_changes: {} },
    { change_type: 'modified', resource_id: datastores[0].id, resource_type: 'datastore', resource_name: datastores[0].name, significance: 'medium', summary: 'Datastore usage increased', property_changes: { usedPct: { old: 84, new: 91 }, freeGB: { old: 9830, new: 5530 } } },
    { change_type: 'modified', resource_id: vms[0].id, resource_type: 'vm', resource_name: 'web01', significance: 'medium', summary: 'VM moved host', property_changes: { host: { old: 'esx03.wld01.vcf.example', new: 'esx01.wld01.vcf.example' } } },
    { change_type: 'modified', resource_id: vms[4].id, resource_type: 'vm', resource_name: 'app01', significance: 'medium', summary: 'VM moved host', property_changes: { host: { old: 'esx03.wld01.vcf.example', new: 'esx02.wld01.vcf.example' } } },
    { change_type: 'modified', resource_id: vms[28].id, resource_type: 'vm', resource_name: 'test-win11', significance: 'medium', summary: 'VM powered off', property_changes: { powerState: { old: 'poweredOn', new: 'poweredOff' }, ipAddress: { old: '10.16.24.38', new: null } } },
    { change_type: 'added', resource_id: vms[29].id, resource_type: 'vm', resource_name: 'test-rhel9', significance: 'low', summary: 'VM created', property_changes: {} },
    { change_type: 'modified', resource_id: hosts[4].id, resource_type: 'host', resource_name: hosts[4].name, significance: 'low', summary: 'Host resource usage changed', property_changes: { cpuUsagePct: { old: 58, new: 71 }, memUsagePct: { old: 79, new: 84 } } },
    { change_type: 'modified', resource_id: vms[24].id, resource_type: 'vm', resource_name: 'gitlab01', significance: 'low', summary: 'Snapshot count changed', property_changes: { snapshots: { old: 2, new: 3 } } },
    { change_type: 'modified', resource_id: vms[18].id, resource_type: 'vm', resource_name: 'log02', significance: 'low', summary: 'Tools status changed', property_changes: { toolsStatus: { old: 'toolsOk', new: 'toolsNotRunning' } } },
    { change_type: 'modified', resource_id: vc.id, resource_type: 'vcenter', resource_name: vc.name, significance: 'low', summary: 'vCenter build changed', property_changes: { build: { old: '24755230', new: '24805960' } } },
  ]

  const scans: ScanRun[] = [
    { id: 'scan-vc01-9', connection_id: src, started: minutesAgo(2), finished: minutesAgo(1.7), status: 'ok', error: null, snapshot_id: 'snap-vc01-004', trigger: 'scheduled' },
    { id: 'scan-vc01-8', connection_id: src, started: minutesAgo(22), finished: minutesAgo(21.5), status: 'ok', error: null, snapshot_id: 'snap-vc01-003', trigger: 'manual' },
    { id: 'scan-vc01-7', connection_id: src, started: minutesAgo(42), finished: minutesAgo(41), status: 'error', error: 'timeout after 60s connecting to vc-wld01.wld01.vcf.example:443', snapshot_id: null, trigger: 'scheduled' },
  ]

  return {
    connection: { id: src, name: 'Centennial Lab (wld01)', host: 'vc-wld01.wld01.vcf.example', username: 'administrator@vsphere.local', verify_tls: false, created_at: minutesAgo(400), kind: 'vcenter' },
    schedule: { connection_id: src, interval_minutes: 5, enabled: true, last_run: minutesAgo(2), next_run: new Date(now + 3 * 60_000).toISOString(), last_status: 'ok' },
    resources: r, findings, snapshots, changes, scans,
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
    const h: Resource = { id: id('host', n), type: 'host', name: `${n}.wld01.vcf.example`, source: src, parent_id: cl.id, properties: { connectionState: 'connected', powerState: 'poweredOn', version: '9.0.1', cpuUsagePct: 30 + i * 5, memUsagePct: 50 + i * 4, ntpSynced: true }, relationships: [] }
    r.push(h); return h
  })
  const ds: Resource = { id: id('datastore', 'mgmt-vsan'), type: 'datastore', name: 'mgmt-vsan', source: src, parent_id: dc.id, properties: { type: 'vsan', capacityGB: 40960, freeGB: 22000, usedPct: 46, accessible: true }, relationships: [] }
  const net: Resource = { id: id('network', 'mgmt-vlan1600'), type: 'network', name: 'mgmt-vlan1600', source: src, parent_id: dc.id, properties: { kind: 'DistributedVirtualPortgroup', vlanId: 1600 }, relationships: [] }
  r.push(ds, net)
  const vmNames = ['sddc-manager', 'nsx-mgr-01', 'nsx-mgr-02', 'nsx-mgr-03', 'vc01', 'mgmt-vc', 'ops-01', 'aria-lcm']
  vmNames.forEach((n, i) => r.push({ id: id('vm', n), type: 'vm', name: n, source: src, parent_id: hosts[i % 4].id, properties: { powerState: 'poweredOn', vcpus: 8, memoryGB: 48, toolsStatus: 'toolsOk', datastore: ds.name, network: net.name }, relationships: [{ kind: 'runs_on', target_id: hosts[i % 4].id }] }))
  return {
    connection: { id: src, name: 'Management domain', host: 'vc-mgmt.mgmt.vcf.example', username: 'administrator@vsphere.local', verify_tls: true, created_at: minutesAgo(900), kind: 'vcenter' },
    schedule: { connection_id: src, interval_minutes: 15, enabled: true, last_run: minutesAgo(9), next_run: new Date(now + 6 * 60_000).toISOString(), last_status: 'ok' },
    resources: r,
    findings: [],
    snapshots: [
      { id: 'snap-vc00-002', created_at: minutesAgo(9), label: 'Scheduled', connection_id: src, scheduled: true, resource_count: r.length },
      { id: 'snap-vc00-001', created_at: minutesAgo(130), label: 'Scheduled', connection_id: src, scheduled: true, resource_count: r.length },
    ],
    changes: [
      { change_type: 'modified', resource_id: hosts[1].id, resource_type: 'host', resource_name: hosts[1].name, significance: 'low', summary: 'Host resource usage changed', property_changes: { cpuUsagePct: { old: 31, new: 35 } } },
    ],
    scans: [
      { id: 'scan-vc00-3', connection_id: src, started: minutesAgo(9), finished: minutesAgo(8.6), status: 'ok', error: null, snapshot_id: 'snap-vc00-002', trigger: 'scheduled' },
    ],
  }
}

export const mockState = {
  estates: [buildWorkloadDomain(), buildManagementDomain()] as MockEstate[],
  settings: { retention: 30, assistant: { enabled: true, provider: 'mock', model: 'claude-opus-5', api_key_set: false } } as Settings,
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
