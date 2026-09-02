import type { ReactNode } from 'react'
import { ArrowRight, Boxes, Clock, Database, Hand, HardDrive, Layers, Minus, MonitorSmartphone, Network, Plus, Server } from 'lucide-react'
import type { Finding, Severity, Significance, Change, SnapshotSummary, SnapshotTier, EventCategory } from '@/types'
import { Badge, type Tone } from '@/components/ui'
import { cn } from '@/lib/cn'
import { formatBytes, formatProperty, formatValue, humanKey, isByteKey } from '@/lib/format'

export const severityTone: Record<Severity, Tone> = { critical: 'critical', warning: 'warning', info: 'info' }
export const significanceTone: Record<Significance, Tone> = { high: 'critical', medium: 'warning', low: 'neutral' }
export const significanceLabel: Record<Significance, string> = { high: 'High impact', medium: 'Medium', low: 'Informational' }

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge tone={severityTone[severity]} dot>{severity}</Badge>
}

/* ---------- Snapshot retention tiers ---------- */
export const tierLabel: Record<SnapshotTier, string> = { manual: 'Manual', recent: 'Recent', hourly: 'Hourly', daily: 'Daily' }
const tierTone: Record<SnapshotTier, Tone> = { manual: 'info', recent: 'neutral', hourly: 'neutral', daily: 'neutral' }
// Tier with a fallback for rows written before the retention tiers existed.
export function snapshotTier(s: Pick<SnapshotSummary, 'tier' | 'scheduled'>): SnapshotTier {
  return s.tier ?? (s.scheduled ? 'recent' : 'manual')
}
export function TierBadge({ snapshot }: { snapshot: Pick<SnapshotSummary, 'tier' | 'scheduled'> }) {
  const tier = snapshotTier(snapshot)
  return <Badge tone={tierTone[tier]}>{tier === 'manual' ? <Hand size={11} /> : <Clock size={11} />} {tierLabel[tier]}</Badge>
}

/* ---------- vCenter event categories ---------- */
export const eventCategoryLabel: Record<EventCategory, string> = { user: 'User action', warning: 'Warning', error: 'Error', info: 'Info' }
export const eventCategoryDot: Record<EventCategory, string> = { user: 'bg-accent', warning: 'bg-warning', error: 'bg-critical', info: 'bg-faint' }
export function EventCategoryDot({ category, className }: { category: EventCategory; className?: string }) {
  return <span aria-hidden className={cn('inline-block h-2 w-2 rounded-full shrink-0', eventCategoryDot[category] ?? 'bg-faint', className)} title={eventCategoryLabel[category] ?? category} />
}

export function ResourceIcon({ type, size = 15, className }: { type: string; size?: number; className?: string }) {
  const p = { size, className }
  switch (type) {
    case 'host': return <Server {...p} />
    case 'vm': return <MonitorSmartphone {...p} />
    case 'datastore': return <HardDrive {...p} />
    case 'network': return <Network {...p} />
    case 'cluster': return <Layers {...p} />
    case 'datacenter': return <Boxes {...p} />
    case 'vcenter': return <Database {...p} />
    default: return <Boxes {...p} />
  }
}

export function ResourceTypeLabel({ type }: { type: string }) {
  const map: Record<string, string> = { host: 'Host', vm: 'VM', datastore: 'Datastore', network: 'Network', cluster: 'Cluster', datacenter: 'Datacenter', vcenter: 'vCenter' }
  return <>{map[type] ?? type}</>
}

// old -> new transition. Renders a single value when there is no old side.
export function ValueArrow({ oldValue, newValue, mono = true, stacked = false }: { oldValue?: unknown; newValue: unknown; mono?: boolean; stacked?: boolean }) {
  const hasOld = oldValue !== undefined
  const f = mono ? 'font-mono text-[13px]' : 'text-sm'
  if (!hasOld) return <span className={cn(f, 'text-fg break-all')}>{formatValue(newValue)}</span>
  if (stacked) {
    return (
      <div className="flex flex-col items-start gap-1">
        <span className={cn(f, 'text-muted line-through decoration-muted/60 break-all')}>{formatValue(oldValue)}</span>
        <ArrowRight size={14} className="text-faint rotate-90 ml-1" />
        <span className={cn(f, 'text-fg font-medium break-all')}>{formatValue(newValue)}</span>
      </div>
    )
  }
  return (
    <span className="inline-flex items-center gap-2 flex-wrap">
      <span className={cn(f, 'text-muted break-all')}>{formatValue(oldValue)}</span>
      <ArrowRight size={14} className="text-faint shrink-0" />
      <span className={cn(f, 'text-fg font-medium break-all')}>{formatValue(newValue)}</span>
    </span>
  )
}

function isOldNew(v: unknown): v is { old: unknown; new: unknown } {
  return !!v && typeof v === 'object' && !Array.isArray(v) && 'old' in (v as object) && 'new' in (v as object)
}

export function EvidenceTable({ evidence }: { evidence: Record<string, unknown> }) {
  const entries = Object.entries(evidence)
  if (entries.length === 0) return <p className="text-sm text-muted">No evidence recorded.</p>
  return (
    <dl className="divide-y divide-border rounded-lg border border-border overflow-hidden">
      {entries.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[minmax(0,160px)_1fr] gap-3 px-3.5 py-2.5 bg-surface">
          <dt className="text-[13px] text-muted font-medium truncate" title={k}>{humanKey(k)}</dt>
          <dd className="min-w-0">{isOldNew(v) ? <ValueArrow oldValue={v.old} newValue={v.new} /> : <ValueArrow newValue={v} />}</dd>
        </div>
      ))}
    </dl>
  )
}

export function FindingRow({ finding, onClick, compact }: { finding: Finding; onClick?: () => void; compact?: boolean }) {
  return (
    <button onClick={onClick} className={cn('w-full text-left group rounded-lg border border-border bg-surface hover:border-border-strong hover:bg-surface-2 transition-colors duration-150',
      compact ? 'px-4 py-3' : 'px-5 py-4')}>
      <div className="flex items-start gap-3">
        <span className={cn('mt-1.5 h-2 w-2 rounded-full shrink-0', finding.severity === 'critical' ? 'bg-critical' : finding.severity === 'warning' ? 'bg-warning' : 'bg-info')} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <SeverityBadge severity={finding.severity} />
            {finding.resource_type ? <span className="text-xs text-faint inline-flex items-center gap-1"><ResourceIcon type={finding.resource_type} size={12} /><ResourceTypeLabel type={finding.resource_type} /></span> : null}
          </div>
          <p className={cn('font-semibold tracking-tight mt-1.5', compact ? 'text-sm' : 'text-[15px]')}>{finding.title}</p>
          {!compact ? <p className="text-sm text-muted mt-1 leading-relaxed line-clamp-2">{finding.summary}</p> : null}
        </div>
        <ArrowRight size={16} className="text-faint opacity-0 group-hover:opacity-100 transition-opacity mt-1 shrink-0" />
      </div>
    </button>
  )
}

export function ChangeRow({ change, compact }: { change: Change; compact?: boolean }) {
  const entries = Object.entries(change.property_changes)
  const typeTone: Tone = change.change_type === 'added' ? 'ok' : change.change_type === 'removed' ? 'critical' : 'neutral'
  return (
    <div className={cn('rounded-lg border border-border bg-surface', compact ? 'px-4 py-3' : 'px-5 py-4')}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge tone={significanceTone[change.significance]} dot>{change.significance}</Badge>
            <Badge tone={typeTone}>{change.change_type}</Badge>
            <span className="text-xs text-faint inline-flex items-center gap-1"><ResourceIcon type={change.resource_type} size={12} /><ResourceTypeLabel type={change.resource_type} /></span>
          </div>
          <p className={cn('font-semibold tracking-tight mt-1.5', compact ? 'text-sm' : 'text-[15px]')}>{change.resource_name}</p>
          {change.summary ? <p className="text-sm text-muted mt-0.5">{change.summary}</p> : null}
        </div>
      </div>
      {entries.length > 0 ? <PropertyChanges change={change} compact={compact} className="mt-3" />
        : change.change_type !== 'modified' ? (
          <div className="mt-3 text-[13px] font-mono text-muted">{change.change_type === 'added' ? 'new resource' : 'resource no longer present'}</div>
        ) : null}
    </div>
  )
}

// The property-by-property diff grid shared by ChangeRow and the Changes timeline. List-valued
// properties get the ListDiff rendering, byte counts read as GiB/TiB.
export function PropertyChanges({ change, compact, className }: { change: Change; compact?: boolean; className?: string }) {
  const entries = Object.entries(change.property_changes)
  if (entries.length === 0) {
    return change.change_type !== 'modified'
      ? <div className={cn('text-[13px] font-mono text-muted', className)}>{change.change_type === 'added' ? 'new resource' : 'resource no longer present'}</div>
      : <div className={cn('text-[13px] text-faint', className)}>No property detail recorded.</div>
  }
  return (
    <div className={cn('grid gap-2', compact ? '' : 'sm:grid-cols-2', className)}>
      {entries.map(([k, v]) => {
        const listy = Array.isArray(v.old) || Array.isArray(v.new)
        return (
          <div key={k} className={cn('rounded-md bg-surface-2 border border-border px-3 py-2 min-w-0', listy && !compact && 'sm:col-span-2')}>
            <div className="text-[11px] uppercase tracking-wider text-faint font-semibold mb-1">{humanKey(k)}</div>
            {listy ? <ListDiff propertyKey={k} oldList={asList(v.old)} newList={asList(v.new)} compact={compact} />
              : <ValueArrow oldValue={bytesIfNeeded(k, v.old)} newValue={bytesIfNeeded(k, v.new)} />}
          </div>
        )
      })}
    </div>
  )
}

// Byte-valued properties read as GiB/TiB instead of raw counts.
function bytesIfNeeded(k: string, v: unknown): unknown {
  return isByteKey(k) && typeof v === 'number' ? formatBytes(v) : v
}

/* ---------- List-valued property diffs ---------- */

function asList(v: unknown): unknown[] {
  return Array.isArray(v) ? v : v === null || v === undefined ? [] : [v]
}

const ITEM_KEYS = ['device', 'label', 'name', 'id', 'mac', 'url']
type Item = Record<string, unknown>

// Stable identity for a list item: the first known label field on objects, the value itself on scalars.
function itemKey(v: unknown): string {
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    const o = v as Item
    for (const k of ITEM_KEYS) if (typeof o[k] === 'string' || typeof o[k] === 'number') return String(o[k])
    return JSON.stringify(o)
  }
  return formatValue(v)
}

// Short one-line rendering of a list item ("vmk1 10.16.11.21 mtu 9000 on wld01-vmotion").
function itemLabel(v: unknown): string {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return formatValue(v)
  const o = v as Item
  const head = itemKey(v)
  const rest = Object.entries(o)
    .filter(([k, val]) => val !== null && val !== undefined && val !== '' && String(val) !== head && !ITEM_KEYS.includes(k))
    .map(([k, val]) => typeof val === 'boolean' ? (val ? k : `not ${k}`) : `${fieldLabel(k)} ${formatProperty(k, val)}`)
  return rest.length ? `${head} (${rest.join(', ')})` : head
}

// "capacityBytes" reads as "capacity", "linkSpeedMb" as "link speed".
function fieldLabel(k: string): string {
  return humanKey(k).toLowerCase().replace(/ (bytes|mb)$/, '')
}
function fieldValue(k: string, v: unknown): string {
  return typeof v === 'boolean' ? (v ? 'yes' : 'no') : formatProperty(k, v)
}

interface ItemDiff { key: string; field: string; old: unknown; new: unknown }
interface ListDiffResult { added: unknown[]; removed: unknown[]; modified: ItemDiff[] }

function diffLists(oldList: unknown[], newList: unknown[]): ListDiffResult {
  const oldBy = new Map(oldList.map(v => [itemKey(v), v]))
  const newBy = new Map(newList.map(v => [itemKey(v), v]))
  const added = newList.filter(v => !oldBy.has(itemKey(v)))
  const removed = oldList.filter(v => !newBy.has(itemKey(v)))
  const modified: ItemDiff[] = []
  for (const [key, nv] of newBy) {
    const ov = oldBy.get(key)
    if (ov === undefined || !nv || typeof nv !== 'object' || !ov || typeof ov !== 'object') continue
    const o = ov as Item, n = nv as Item
    for (const f of new Set([...Object.keys(o), ...Object.keys(n)])) {
      if (JSON.stringify(o[f]) !== JSON.stringify(n[f])) modified.push({ key, field: f, old: o[f], new: n[f] })
    }
  }
  return { added, removed, modified }
}

export function ListDiff({ propertyKey, oldList, newList, compact }: { propertyKey: string; oldList: unknown[]; newList: unknown[]; compact?: boolean }) {
  const d = diffLists(oldList, newList)
  const nothing = d.added.length === 0 && d.removed.length === 0 && d.modified.length === 0
  if (nothing) return <span className="text-[13px] font-mono text-muted">{newList.length === 0 ? 'empty' : 'order changed only'}</span>
  const f = compact ? 'text-xs' : 'text-[13px]'
  return (
    <div className="space-y-1.5" data-testid={`listdiff-${propertyKey}`}>
      {d.modified.map((m, i) => (
        <div key={`m-${i}`} className={cn('font-mono flex flex-wrap items-center gap-x-2 gap-y-0.5', f)}>
          <span className="text-fg font-medium">{m.key}</span>
          <span className="text-muted">{fieldLabel(m.field)}</span>
          <span className="text-muted">{fieldValue(m.field, m.old)}</span>
          <ArrowRight size={13} className="text-faint shrink-0" />
          <span className="text-fg font-medium">{fieldValue(m.field, m.new)}</span>
        </div>
      ))}
      {d.added.length > 0 || d.removed.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {d.added.map((v, i) => (
            <span key={`a-${i}`} className={cn('inline-flex items-center gap-1 rounded-md bg-ok-bg text-ok px-2 py-0.5 font-mono break-all', f)}><Plus size={11} className="shrink-0" />{itemLabel(v)}</span>
          ))}
          {d.removed.map((v, i) => (
            <span key={`r-${i}`} className={cn('inline-flex items-center gap-1 rounded-md bg-critical-bg text-critical px-2 py-0.5 font-mono line-through decoration-critical/60 break-all', f)}><Minus size={11} className="shrink-0" />{itemLabel(v)}</span>
          ))}
        </div>
      ) : null}
    </div>
  )
}

export function StatCard({ label, value, sub, icon, tone }: { label: string; value: ReactNode; sub?: ReactNode; icon?: ReactNode; tone?: Tone }) {
  const toneText: Record<Tone, string> = { critical: 'text-critical', warning: 'text-warning', info: 'text-info', ok: 'text-ok', neutral: 'text-fg', accent: 'text-accent' }
  return (
    <div className="rounded-xl border border-border bg-surface shadow-card px-5 py-4 flex flex-col gap-2 min-w-0">
      <div className="flex items-center justify-between text-muted">
        <span className="text-xs font-medium uppercase tracking-wider">{label}</span>
        {icon}
      </div>
      <div className={cn('text-3xl font-semibold tracking-tight tnum', tone ? toneText[tone] : 'text-fg')}>{value}</div>
      {sub ? <div className="text-sm text-muted">{sub}</div> : null}
    </div>
  )
}
