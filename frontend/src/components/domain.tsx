import type { ReactNode } from 'react'
import { ArrowRight, Boxes, Database, HardDrive, Layers, MonitorSmartphone, Network, Server } from 'lucide-react'
import type { Finding, Severity, Significance, Change } from '@/types'
import { Badge, type Tone } from '@/components/ui'
import { cn } from '@/lib/cn'
import { formatBytes, formatValue, humanKey, isByteKey } from '@/lib/format'

export const severityTone: Record<Severity, Tone> = { critical: 'critical', warning: 'warning', info: 'info' }
export const significanceTone: Record<Significance, Tone> = { high: 'critical', medium: 'warning', low: 'neutral' }
export const significanceLabel: Record<Significance, string> = { high: 'High impact', medium: 'Medium', low: 'Informational' }

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge tone={severityTone[severity]} dot>{severity}</Badge>
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
          <p className="text-sm text-muted mt-0.5">{change.summary}</p>
        </div>
      </div>
      {entries.length > 0 ? (
        <div className={cn('mt-3 grid gap-2', compact ? '' : 'sm:grid-cols-2')}>
          {entries.map(([k, v]) => (
            <div key={k} className="rounded-md bg-surface-2 border border-border px-3 py-2">
              <div className="text-[11px] uppercase tracking-wider text-faint font-semibold mb-1">{humanKey(k)}</div>
              <ValueArrow oldValue={bytesIfNeeded(k, v.old)} newValue={bytesIfNeeded(k, v.new)} />
            </div>
          ))}
        </div>
      ) : change.change_type !== 'modified' ? (
        <div className="mt-3 text-[13px] font-mono text-muted">{change.change_type === 'added' ? 'new resource' : 'resource no longer present'}</div>
      ) : null}
    </div>
  )
}

// Byte-valued properties read as GiB/TiB instead of raw counts.
function bytesIfNeeded(k: string, v: unknown): unknown {
  return isByteKey(k) && typeof v === 'number' ? formatBytes(v) : v
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
