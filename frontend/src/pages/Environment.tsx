import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, ChevronDown, ChevronRight, Globe, History, Plug, ShieldAlert, ShieldCheck } from 'lucide-react'
import type { EnvironmentConnection, Finding, Significance } from '@/types'
import { getChangesMinSignificance, getEnvironmentChanges } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { useAppState } from '@/state/AppState'
import { Badge, Button, Card, EmptyState, ErrorState, Input, PageHeader, Segmented, Skeleton, Spinner } from '@/components/ui'
import { FindingRow, StatCard } from '@/components/domain'
import { ChangeLogRow, SignificanceSummary as Summary } from '@/components/changes/ChangeLogRow'
import { formatDateTime, groupByDay } from '@/lib/format'
import { cn } from '@/lib/cn'

type Preset = 'cycle' | '24h' | '7d' | 'custom'
const PRESETS: Array<{ value: Preset; label: string }> = [
  { value: 'cycle', label: 'Since last scan cycle' },
  { value: '24h', label: 'Last 24 h' },
  { value: '7d', label: 'Last 7 days' },
  { value: 'custom', label: 'Custom range' },
]

// datetime-local wants local wall-clock "YYYY-MM-DDTHH:mm"; the API wants ISO with an offset.
function toLocalInput(ms: number): string {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}
function fromLocalInput(v: string): string | null {
  const t = new Date(v).getTime()
  return Number.isNaN(t) ? null : new Date(t).toISOString()
}
// Rounded to the minute so re-renders inside the same minute reuse the query key.
function minuteAgo(ms: number, now = Date.now()): string {
  const t = now - ms
  return new Date(t - (t % 60_000)).toISOString()
}

export default function EnvironmentPage() {
  const { connections, refreshKey, setSelectedId } = useAppState()
  const nav = useNavigate()
  const [preset, setPreset] = useState<Preset>('cycle')
  const [customFrom, setCustomFrom] = useState(() => toLocalInput(Date.now() - 7 * 86_400_000))
  const [customTo, setCustomTo] = useState(() => toLocalInput(Date.now()))
  const [applied, setApplied] = useState<{ since: string; until: string } | null>(null)

  // Significance floor: starts from the Settings default, then follows the control on this page.
  const [minSig, setMinSig] = useState<Significance | null>(null)
  useEffect(() => {
    let cancelled = false
    getChangesMinSignificance().then(v => { if (!cancelled) setMinSig(v) })
    return () => { cancelled = true }
  }, [])

  const range = useMemo(() => {
    if (preset === 'cycle') return { since: null, until: null }
    if (preset === '24h') return { since: minuteAgo(86_400_000), until: null }
    if (preset === '7d') return { since: minuteAgo(7 * 86_400_000), until: null }
    return applied
  }, [preset, applied, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const result = useAsync(
    () => getEnvironmentChanges({ since: range?.since, until: range?.until, minSignificance: minSig as Significance }),
    [range, minSig, refreshKey], minSig !== null && range !== null,
  )
  const data = result.data
  const customInvalid = preset === 'custom' && (!fromLocalInput(customFrom) || !fromLocalInput(customTo) || new Date(customFrom).getTime() > new Date(customTo).getTime())
  const apply = () => {
    const since = fromLocalInput(customFrom), until = fromLocalInput(customTo)
    if (since && until && since <= until) setApplied({ since, until })
  }

  // Changes and Health work on one connection; select it before jumping there.
  const select = (id: string) => setSelectedId(id)
  const compare = (connectionId: string, from: string, to: string) => {
    select(connectionId)
    nav(`/changes?view=compare&from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`)
  }

  return (
    <div className="anim-fade-up">
      <PageHeader title="Environment Changes" subtitle="What changed across every connection between two points in time, rolled up from each scan's recorded differences"
        actions={minSig ? (
          <Segmented value={minSig} onChange={setMinSig} options={[
            { value: 'low', label: 'All' },
            { value: 'medium', label: <><span className="h-1.5 w-1.5 rounded-full bg-warning" />Medium and above</> },
            { value: 'high', label: <><span className="h-1.5 w-1.5 rounded-full bg-critical" />High only</> },
          ]} />
        ) : null} />

      <Card className="px-5 py-4 mb-6">
        <div className="flex flex-wrap items-center gap-3">
          <Segmented value={preset} onChange={p => { setPreset(p); if (p === 'custom') setApplied(null) }} options={PRESETS} />
          {result.loading && data ? <span className="ml-auto inline-flex items-center gap-2 text-sm text-muted"><Spinner className="h-4 w-4" /> Loading window</span> : null}
          {data && !result.loading ? (
            <span className="text-sm text-muted ml-auto tnum" title={`${data.since} to ${data.until}`}>
              {formatDateTime(data.since)} to {formatDateTime(data.until)}
              {data.window === 'last_cycle' ? <span className="text-faint"> (from the oldest of each connection's latest scan)</span> : null}
            </span>
          ) : null}
        </div>
        {preset === 'custom' ? (
          <div className="mt-4 grid gap-3 md:grid-cols-[1fr_1fr_auto] items-end">
            <label className="block">
              <span className="block text-[11px] font-semibold uppercase tracking-wider text-faint mb-1.5">From</span>
              <Input type="datetime-local" value={customFrom} onChange={e => setCustomFrom(e.target.value)} aria-label="From" />
            </label>
            <label className="block">
              <span className="block text-[11px] font-semibold uppercase tracking-wider text-faint mb-1.5">To</span>
              <Input type="datetime-local" value={customTo} onChange={e => setCustomTo(e.target.value)} aria-label="To" />
            </label>
            <Button variant="primary" onClick={apply} disabled={customInvalid}>Apply</Button>
            {customInvalid ? <p role="status" className="md:col-span-3 flex items-center gap-1.5 text-xs text-warning"><AlertTriangle size={13} /> From must be a valid time no later than To.</p> : null}
            {!applied && !customInvalid ? <p className="md:col-span-3 text-xs text-faint">Pick a range and press Apply.</p> : null}
          </div>
        ) : null}
      </Card>

      {preset === 'custom' && !applied ? null
        : connections.length === 0 && !data ? (
        <Card><EmptyState icon={<Plug size={20} />} title="No connections yet" body="Add a vCenter and run a scan. Each scan records what changed, and this page rolls those records up across the estate."
          action={<Button variant="primary" onClick={() => nav('/connections')}>Add vCenter</Button>} /></Card>
      ) : result.error ? <Card><ErrorState title="Environment changes unavailable" error={result.error} onRetry={result.reload} /></Card>
        : !data ? (
          <div className="space-y-4"><div className="grid gap-4 md:grid-cols-5">{[0, 1, 2, 3, 4].map(i => <Skeleton key={i} className="h-24" />)}</div>{[0, 1].map(i => <Skeleton key={i} className="h-40" />)}</div>
        ) : (
          <div className={cn(result.loading && 'opacity-50 transition-opacity')} aria-busy={result.loading}>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5 mb-8">
              <StatCard label="Connections covered" value={<>{data.totals.covered}<span className="text-lg text-muted font-medium"> of {data.totals.connections}</span></>}
                sub={data.totals.no_data > 0 ? `${data.totals.no_data} with no data in window` : 'every connection scanned in this window'} icon={<Globe size={16} />}
                tone={data.totals.no_data > 0 ? 'warning' : undefined} />
              <StatCard label="Changes" value={data.totals.changes.total} icon={<History size={16} />}
                sub={<span className="flex flex-wrap gap-x-3"><Summary n={data.totals.changes.high} label="high" dot="bg-critical" /><Summary n={data.totals.changes.medium} label="medium" dot="bg-warning" /><Summary n={data.totals.changes.low} label="low" dot="bg-faint" /></span>} />
              <StatCard label="High impact" value={data.totals.changes.high} tone={data.totals.changes.high > 0 ? 'critical' : undefined} icon={<AlertTriangle size={16} />}
                sub={data.min_significance === 'high' ? 'showing high only' : 'across all connections'} />
              <StatCard label="Findings appeared" value={data.totals.findings_appeared} tone={data.totals.findings_appeared > 0 ? 'warning' : undefined} icon={<ShieldAlert size={16} />}
                sub="new since the start of the window" />
              <StatCard label="Findings cleared" value={data.totals.findings_cleared} tone={data.totals.findings_cleared > 0 ? 'ok' : undefined} icon={<ShieldCheck size={16} />}
                sub="present at the start, gone at the end" />
            </div>

            {data.connections.length === 0 ? (
              <Card><EmptyState icon={<Plug size={20} />} title="No connections yet" body="Add a vCenter and run a scan to start recording changes."
                action={<Button variant="primary" onClick={() => nav('/connections')}>Add vCenter</Button>} /></Card>
            ) : (
              <div className="space-y-6">
                {data.connections.map(c => (
                  <ConnectionSection key={c.connection_id} section={c} minSig={data.min_significance} since={data.since} until={data.until}
                    onCompare={(from, to) => compare(c.connection_id, from, to)}
                    onFinding={() => { select(c.connection_id); nav('/health') }} />
                ))}
              </div>
            )}
          </div>
        )}
    </div>
  )
}

function ConnectionSection({ section, minSig, since, until, onCompare, onFinding }: { section: EnvironmentConnection; minSig: Significance; since: string; until: string; onCompare: (from: string, to: string) => void; onFinding: (f: Finding) => void }) {
  const [openIds, setOpenIds] = useState<Set<string>>(new Set())
  const [collapsed, setCollapsed] = useState(false)
  const toggle = (id: string) => setOpenIds(s => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const groups = useMemo(() => groupByDay(section.changes, r => r.observed_at), [section.changes])
  const appeared = section.findings?.appeared ?? []
  const cleared = section.findings?.cleared ?? []

  return (
    <section aria-label={section.name}>
      <button onClick={() => setCollapsed(v => !v)} aria-expanded={!collapsed}
        className="w-full text-left flex flex-wrap items-center gap-x-3 gap-y-1 mb-2.5 group">
        {collapsed ? <ChevronRight size={15} className="text-faint" /> : <ChevronDown size={15} className="text-faint" />}
        <span className="text-[15px] font-semibold tracking-tight">{section.name}</span>
        <span className="text-xs text-faint font-mono">{section.host}</span>
        {section.has_data ? (
          <span className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className="text-xs text-faint tnum">{section.snapshots_in_window} {section.snapshots_in_window === 1 ? 'scan' : 'scans'} in window</span>
            <Summary n={section.counts.high} label="high" dot="bg-critical" />
            <Summary n={section.counts.medium} label="medium" dot="bg-warning" />
            <Summary n={section.counts.low} label="low" dot="bg-faint" />
            {appeared.length ? <Badge tone="warning" dot>{appeared.length} {appeared.length === 1 ? 'finding' : 'findings'} appeared</Badge> : null}
            {cleared.length ? <Badge tone="ok" dot>{cleared.length} cleared</Badge> : null}
          </span>
        ) : <Badge tone="neutral" className="ml-auto">No data in window</Badge>}
      </button>

      {collapsed ? null : !section.has_data ? (
        <Card className="px-5 py-4 text-sm text-muted">
          No scan of this connection completed between {formatDateTime(since)} and {formatDateTime(until)}, so nothing can be said about it for this window. Widen the range or run Scan Now.
        </Card>
      ) : (
        <div className="space-y-4">
          {section.changes.length === 0 ? (
            <Card className="px-5 py-4 text-sm text-muted">
              {minSig !== 'low' ? 'No changes at this significance in the window. Lower significance changes may exist; switch the filter to All.'
                : section.findings === null ? 'Scanned in this window, but there is no earlier snapshot to compare against yet. The next scan will start recording differences.'
                : 'Scanned in this window and nothing differed from the previous snapshot.'}
            </Card>
          ) : (
            <Card className="overflow-hidden">
              {groups.map(g => (
                <div key={g.key}>
                  <div className="px-4 py-1.5 bg-surface-2/60 border-y border-border first:border-t-0 text-[11px] font-semibold uppercase tracking-wider text-faint flex items-baseline gap-2">{g.label} <span className="tnum font-medium normal-case tracking-normal">{g.items.length}</span></div>
                  <div className="divide-y divide-border">
                    {g.items.map(r => <ChangeLogRow key={r.id} row={r} open={openIds.has(r.id)} onToggle={() => toggle(r.id)} onCompare={onCompare} />)}
                  </div>
                </div>
              ))}
              {section.truncated ? (
                <div className="px-4 py-2.5 text-xs text-muted border-t border-border flex items-center gap-2">
                  <AlertTriangle size={13} className="text-warning" /> Showing the newest {section.changes.length} of {section.counts.total} changes. The counts above cover the whole window; open the Changes page for this connection to page through the rest.
                </div>
              ) : null}
            </Card>
          )}

          {section.findings && (appeared.length > 0 || cleared.length > 0) ? (
            <div className="grid gap-4 lg:grid-cols-2">
              <FindingsList title="Findings that appeared" findings={appeared} tone="warning" onFinding={onFinding} note={`absent at ${formatDateTime(section.findings.baseline_at)}, present at ${formatDateTime(section.findings.end_at)}`} />
              <FindingsList title="Findings that cleared" findings={cleared} tone="ok" onFinding={onFinding} note={`present at ${formatDateTime(section.findings.baseline_at)}, gone at ${formatDateTime(section.findings.end_at)}`} />
            </div>
          ) : null}
        </div>
      )}
    </section>
  )
}

function FindingsList({ title, findings, tone, note, onFinding }: { title: string; findings: Finding[]; tone: 'warning' | 'ok'; note: string; onFinding: (f: Finding) => void }) {
  if (findings.length === 0) return <Card className="px-5 py-4 text-sm text-muted"><span className="font-medium text-fg">{title}:</span> none</Card>
  return (
    <Card className="px-4 py-3">
      <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-faint mb-2.5">
        <span className={cn('h-2 w-2 rounded-full', tone === 'warning' ? 'bg-warning' : 'bg-ok')} />{title} <span className="tnum">{findings.length}</span>
        <span className="ml-auto font-normal normal-case tracking-normal">{note}</span>
      </h3>
      <div className="space-y-2">
        {findings.map(f => <FindingRow key={f.id} finding={f} compact onClick={() => onFinding(f)} />)}
      </div>
    </Card>
  )
}
