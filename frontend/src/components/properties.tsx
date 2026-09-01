import { useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { formatBytes, formatProperty, formatValue, humanKey } from '@/lib/format'
import { cn } from '@/lib/cn'

// Grouped, readable rendering of Resource.properties (docs/PROPERTIES.md). Keys the
// contract names land in a named group; anything else lands in a collapsed "Other"
// group so a new collector field is never hidden.

type GroupId = 'identity' | 'state' | 'capacity' | 'network' | 'storage' | 'cluster' | 'other'

const GROUP_ORDER: GroupId[] = ['identity', 'state', 'capacity', 'network', 'storage', 'cluster', 'other']
const GROUP_TITLE: Record<GroupId, string> = {
  identity: 'Identity', state: 'State', capacity: 'Capacity', network: 'Network', storage: 'Storage', cluster: 'Cluster config', other: 'Other',
}

// Key order inside a group follows this list; unknown keys go last in source order.
const GROUP_KEYS: Record<Exclude<GroupId, 'other'>, string[]> = {
  identity: [
    'name', 'version', 'build', 'apiVersion', 'apiType', 'instanceUuid', 'osType',
    'model', 'vendor', 'biosVersion', 'hardwareVersion',
    'guestFullName', 'guestHostname', 'guestIp', 'guestState', 'toolsVersion', 'template', 'annotation',
    'datacenter', 'cluster', 'host', 'resourcePool', 'folder',
    'type', 'kind', 'url', 'vmfsVersion', 'switch', 'vlan', 'vlanId', 'numPorts', 'transportZone',
    'hostCount', 'numHosts', 'numVms', 'hosts', 'moref',
  ],
  state: [
    'connectionState', 'powerState', 'maintenanceMode', 'lockdownMode', 'overallStatus', 'accessible',
    'uptimeSeconds', 'bootTime', 'toolsStatus', 'multipleHostAccess', 'exists', 'ntpSynced', 'uptimeDays',
  ],
  capacity: [
    'cpuMhz', 'numCpuCores', 'cpuCores', 'memoryBytes', 'memoryGB', 'numCpu', 'vcpus', 'memoryMB',
    'cpuReservationMhz', 'memReservationMB', 'totalCpuMhz', 'totalMemoryBytes',
    'capacity', 'freeSpace', 'capacityGB', 'freeGB', 'usedPct', 'storageCommittedBytes', 'cpuUsagePct', 'memUsagePct',
  ],
  network: ['vmkernelAdapters', 'physicalNics', 'nics', 'standardSwitches', 'networks', 'network', 'ntpServers', 'dnsServers', 'vmkernelNics', 'ipAddress'],
  storage: ['disks', 'datastores', 'datastore', 'snapshotCount', 'snapshots', 'oldestSnapshotTime', 'oldestSnapshotDays'],
  cluster: ['drsEnabled', 'drsAutomationLevel', 'drsBehavior', 'haEnabled', 'haAdmissionControl', 'evcMode', 'vsanEnabled', 'ruleCount'],
}

const KEY_GROUP = new Map<string, GroupId>()
for (const [g, keys] of Object.entries(GROUP_KEYS) as Array<[Exclude<GroupId, 'other'>, string[]]>) for (const k of keys) KEY_GROUP.set(k, g)

// Table layouts for the nested list properties. Columns not present on an item are skipped.
interface Column { key: string; label: string; mono?: boolean; render?: (v: unknown) => ReactNode }
const yesNo = (v: unknown) => v === true ? 'yes' : v === false ? 'no' : formatValue(v)
const TABLES: Record<string, Column[]> = {
  vmkernelAdapters: [
    { key: 'device', label: 'Device', mono: true }, { key: 'ip', label: 'IP', mono: true },
    { key: 'mtu', label: 'MTU', mono: true }, { key: 'portgroup', label: 'Portgroup' },
  ],
  physicalNics: [
    { key: 'device', label: 'Device', mono: true }, { key: 'mac', label: 'MAC', mono: true },
    { key: 'linkSpeedMb', label: 'Link', render: v => typeof v === 'number' ? (v >= 1000 ? `${v / 1000} Gb` : `${v} Mb`) : formatValue(v) },
  ],
  nics: [
    { key: 'label', label: 'Adapter' }, { key: 'mac', label: 'MAC', mono: true },
    { key: 'network', label: 'Network' }, { key: 'connected', label: 'Connected', render: yesNo },
  ],
  disks: [
    { key: 'label', label: 'Disk' }, { key: 'capacityBytes', label: 'Size', mono: true, render: v => typeof v === 'number' ? formatBytes(v) : formatValue(v) },
    { key: 'datastore', label: 'Datastore' }, { key: 'thin', label: 'Thin', render: yesNo },
  ],
}

// Friendlier labels where the humanized key reads badly ("Uptime Seconds", "Num Cpu Cores").
const LABELS: Record<string, string> = {
  uptimeSeconds: 'Uptime', bootTime: 'Booted', memoryBytes: 'Memory', memoryMB: 'Memory', numCpuCores: 'CPU cores', numCpu: 'vCPUs', cpuMhz: 'CPU clock',
  cpuUsagePct: 'CPU usage', memUsagePct: 'Memory usage', cpuReservationMhz: 'CPU reservation', memReservationMB: 'Memory reservation',
  totalCpuMhz: 'Total CPU', totalMemoryBytes: 'Total memory', storageCommittedBytes: 'Storage committed', usedPct: 'Used', freeSpace: 'Free space',
  guestIp: 'Guest IP', guestFullName: 'Guest OS', guestHostname: 'Guest hostname', guestState: 'Guest state', toolsStatus: 'Tools status', toolsVersion: 'Tools version',
  vmkernelAdapters: 'VMkernel adapters', physicalNics: 'Physical NICs', nics: 'NICs', ntpServers: 'NTP servers', dnsServers: 'DNS servers', ipAddress: 'IP address',
  drsEnabled: 'DRS enabled', drsAutomationLevel: 'DRS automation', drsBehavior: 'DRS behavior', haEnabled: 'HA enabled', haAdmissionControl: 'HA admission control', evcMode: 'EVC mode', vsanEnabled: 'vSAN enabled', ruleCount: 'Rules',
  hardwareVersion: 'Hardware version', apiVersion: 'API version', instanceUuid: 'Instance UUID', osType: 'OS type', biosVersion: 'BIOS version', vmfsVersion: 'VMFS version',
  vlan: 'VLAN', vlanId: 'VLAN ID', numPorts: 'Ports', hostCount: 'Host count', numHosts: 'Host count', numVms: 'VMs', snapshotCount: 'Snapshots', oldestSnapshotTime: 'Oldest snapshot', url: 'URL',
}
export function propertyLabel(k: string): string { return LABELS[k] ?? humanKey(k) }

const CHIP_KEYS = new Set(['ntpServers', 'dnsServers', 'datastores', 'networks', 'standardSwitches', 'hosts', 'vmkernelNics'])

// Keys whose value reads better as a status dot plus text.
function stateTone(k: string, v: unknown): string | null {
  if (k === 'connectionState') return v === 'connected' ? 'bg-ok' : v === 'disconnected' || v === 'notResponding' ? 'bg-critical' : 'bg-warning'
  if (k === 'powerState') return v === 'poweredOn' ? 'bg-ok' : v === 'poweredOff' ? 'bg-faint' : 'bg-warning'
  if (k === 'toolsStatus') return v === 'toolsOk' ? 'bg-ok' : v === 'toolsOld' ? 'bg-warning' : 'bg-critical'
  if (k === 'overallStatus') return v === 'green' ? 'bg-ok' : v === 'yellow' ? 'bg-warning' : v === 'red' ? 'bg-critical' : 'bg-faint'
  if (k === 'maintenanceMode' || k === 'lockdownMode') return v === true || (typeof v === 'string' && v !== 'lockdownDisabled' && v !== 'normal') ? 'bg-warning' : null
  if (k === 'accessible') return v === true ? 'bg-ok' : v === false ? 'bg-critical' : null
  return null
}

function lockdownLabel(v: unknown): string {
  if (v === 'lockdownDisabled') return 'disabled'
  if (v === 'lockdownNormal') return 'normal'
  if (v === 'lockdownStrict') return 'strict'
  return formatValue(v)
}

export function groupProperties(props: Record<string, unknown>): Array<{ id: GroupId; entries: Array<[string, unknown]> }> {
  const buckets = new Map<GroupId, Array<[string, unknown]>>()
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined) continue
    const g = KEY_GROUP.get(k) ?? 'other'
    const list = buckets.get(g) ?? []
    list.push([k, v]); buckets.set(g, list)
  }
  const out: Array<{ id: GroupId; entries: Array<[string, unknown]> }> = []
  for (const id of GROUP_ORDER) {
    const entries = buckets.get(id)
    if (!entries || entries.length === 0) continue
    if (id !== 'other') {
      const order = GROUP_KEYS[id]
      entries.sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
    }
    out.push({ id, entries })
  }
  return out
}

export function PropertyGroups({ properties }: { properties: Record<string, unknown> }) {
  const groups = groupProperties(properties)
  if (groups.length === 0) return <p className="text-sm text-faint">None collected.</p>
  return (
    <div className="space-y-5">
      {groups.map(g => g.id === 'other'
        ? <OtherGroup key={g.id} entries={g.entries} />
        : (
          <section key={g.id} data-testid={`propgroup-${g.id}`}>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-faint mb-1">{GROUP_TITLE[g.id]}</h3>
            <dl className="divide-y divide-border">{g.entries.map(([k, v]) => <PropertyRow key={k} k={k} v={v} />)}</dl>
          </section>
        ))}
    </div>
  )
}

function OtherGroup({ entries }: { entries: Array<[string, unknown]> }) {
  const [open, setOpen] = useState(false)
  return (
    <section data-testid="propgroup-other">
      <button onClick={() => setOpen(o => !o)} className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wider text-faint hover:text-fg mb-1">
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}Other <span className="tnum normal-case tracking-normal font-medium">{entries.length}</span>
      </button>
      {open ? <dl className="divide-y divide-border">{entries.map(([k, v]) => <PropertyRow key={k} k={k} v={v} />)}</dl> : null}
    </section>
  )
}

function PropertyRow({ k, v }: { k: string; v: unknown }) {
  const table = TABLES[k]
  const isList = Array.isArray(v)
  const wide = isList && (table !== undefined || CHIP_KEYS.has(k) || v.length > 3)
  if (wide) {
    return (
      <div className="py-2">
        <dt className="text-[13px] text-muted mb-1.5 flex items-baseline gap-2" title={k}>{propertyLabel(k)} <span className="text-xs text-faint tnum">{v.length}</span></dt>
        <dd className="min-w-0">{v.length === 0 ? <span className="text-[13px] font-mono text-warning">none</span> : table ? <ListTable rows={v} cols={table} /> : <Chips items={v} />}</dd>
      </div>
    )
  }
  return (
    <div className="grid grid-cols-[140px_1fr] gap-3 py-2">
      <dt className="text-[13px] text-muted truncate" title={k}>{propertyLabel(k)}</dt>
      <dd className="text-[13px] font-mono break-all min-w-0"><Scalar k={k} v={v} /></dd>
    </div>
  )
}

function Scalar({ k, v }: { k: string; v: unknown }) {
  const dot = stateTone(k, v)
  const text = k === 'lockdownMode' ? lockdownLabel(v) : formatProperty(k, v)
  if (v === null || v === undefined) return <span className="text-faint">none</span>
  if (Array.isArray(v)) return v.length === 0 ? <span className="text-faint">none</span> : <Chips items={v} />
  if (typeof v === 'object') return <span className="text-muted">{formatValue(v)}</span>
  return <span className={cn('inline-flex items-center gap-1.5', typeof v === 'boolean' && 'text-muted')}>{dot ? <span className={cn('h-1.5 w-1.5 rounded-full shrink-0', dot)} /> : null}{text}</span>
}

function Chips({ items }: { items: unknown[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((it, i) => <span key={i} className="inline-flex items-center rounded-md bg-surface-3 text-fg px-2 py-0.5 text-xs font-mono break-all">{typeof it === 'object' && it !== null ? formatValue(it) : String(it)}</span>)}
    </div>
  )
}

function ListTable({ rows, cols }: { rows: unknown[]; cols: Column[] }) {
  const objs = rows.filter((r): r is Record<string, unknown> => !!r && typeof r === 'object' && !Array.isArray(r))
  if (objs.length !== rows.length) return <Chips items={rows} />
  const present = cols.filter(c => objs.some(o => o[c.key] !== undefined))
  const extra = new Set<string>()
  for (const o of objs) for (const k of Object.keys(o)) if (!cols.some(c => c.key === k)) extra.add(k)
  const all: Column[] = [...present, ...[...extra].map(k => ({ key: k, label: humanKey(k) }))]
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-surface-2 text-left text-faint">
            {all.map(c => <th key={c.key} className="px-2.5 py-1.5 font-semibold uppercase tracking-wider text-[10px] whitespace-nowrap">{c.label}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {objs.map((o, i) => (
            <tr key={i}>
              {all.map(c => {
                const val = o[c.key]
                const text = val === null || val === undefined ? <span className="text-faint">none</span> : c.render ? c.render(val) : formatProperty(c.key, val)
                return <td key={c.key} className={cn('px-2.5 py-1.5 align-top', c.mono ? 'font-mono whitespace-nowrap' : 'break-words')}>{text}</td>
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
