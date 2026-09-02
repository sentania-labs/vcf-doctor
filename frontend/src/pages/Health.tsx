import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckCircle2, Lightbulb, Search, Sparkles, Terminal } from 'lucide-react'
import type { Finding, Severity } from '@/types'
import { getEvents, getFindingRelated, getFindings, getResources, getSnapshots } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { useAppState } from '@/state/AppState'
import { useAssistantDrawer } from '@/state/AssistantState'
import { Button, Card, Drawer, EmptyState, ErrorState, PageHeader, Segmented, Skeleton } from '@/components/ui'
import { EvidenceTable, EventCategoryDot, FindingRow, ResourceIcon, ResourceTypeLabel, SeverityBadge } from '@/components/domain'
import { formatDateTime, formatTime, relativeTime } from '@/lib/format'
import type { AssistantTask, Change, Event, RelatedWindow, Resource } from '@/types'

type Filter = 'all' | Severity

export default function HealthPage() {
  const { connectionId, refreshKey } = useAppState()
  const [filter, setFilter] = useState<Filter>('all')
  const [params, setParams] = useSearchParams()
  const findings = useAsync(() => getFindings(connectionId), [connectionId, refreshKey])
  const selectedId = params.get('finding')
  const selected = useMemo(() => findings.data?.find(f => f.id === selectedId) ?? null, [findings.data, selectedId])

  const list = useMemo(() => {
    const all = findings.data ?? []
    const order: Record<Severity, number> = { critical: 0, warning: 1, info: 2 }
    return (filter === 'all' ? all : all.filter(f => f.severity === filter)).slice().sort((a, b) => order[a.severity] - order[b.severity])
  }, [findings.data, filter])

  const counts = useMemo(() => {
    const c = { all: 0, critical: 0, warning: 0, info: 0 }
    for (const f of findings.data ?? []) { c.all++; c[f.severity]++ }
    return c
  }, [findings.data])

  const open = (f: Finding) => setParams(p => { p.set('finding', f.id); return p }, { replace: true })
  const close = () => setParams(p => { p.delete('finding'); return p }, { replace: true })

  return (
    <div className="anim-fade-up">
      <PageHeader title="Health" subtitle="Deterministic diagnostic findings from the latest snapshot"
        actions={
          <Segmented value={filter} onChange={setFilter} options={[
            { value: 'all', label: <>All <Count n={counts.all} /></> },
            { value: 'critical', label: <><span className="h-1.5 w-1.5 rounded-full bg-critical" />Critical <Count n={counts.critical} /></> },
            { value: 'warning', label: <><span className="h-1.5 w-1.5 rounded-full bg-warning" />Warning <Count n={counts.warning} /></> },
            { value: 'info', label: <><span className="h-1.5 w-1.5 rounded-full bg-info" />Info <Count n={counts.info} /></> },
          ]} />
        } />

      {findings.error && !findings.data ? <Card><ErrorState title="Findings unavailable" error={findings.error} onRetry={findings.reload} /></Card>
        : findings.loading && !findings.data ? <div className="space-y-3">{[0, 1, 2, 3].map(i => <Skeleton key={i} className="h-24" />)}</div>
        : list.length === 0 ? (
          <Card><EmptyState icon={<CheckCircle2 size={20} className="text-ok" />} title={filter === 'all' ? 'No findings' : `No ${filter} findings`}
            body={filter === 'all' ? 'Every diagnostic check passed on the latest snapshot.' : 'Nothing at this severity. Try another filter.'} /></Card>
        ) : (
          <div className="grid gap-3 max-w-4xl">
            {list.map(f => <FindingRow key={f.id} finding={f} onClick={() => open(f)} />)}
          </div>
        )}

      <FindingDrawer finding={selected} onClose={close} />
    </div>
  )
}

function Count({ n }: { n: number }) {
  return <span className="ml-0.5 rounded bg-surface-3 px-1.5 text-[11px] text-muted tnum">{n}</span>
}

interface Related {
  changes: Change[]; resources: Resource[]; events: Event[]
  window: { since: string; until: string | null } | null
  scope: RelatedWindow | null  // what the backend actually looked at; null while loading or when the lookup failed
}

// One line saying which window the related changes come from, so nobody mistakes "0 changes" for "nothing to see".
function describeScope(scope: RelatedWindow): string {
  if (scope.basis === 'first_observed' && scope.since) {
    const scans = `${scope.scans_present} ${scope.scans_present === 1 ? 'scan' : 'scans'}`
    const capped = scope.capped ? ', capped' : ''
    return scope.first_observed && scope.first_observed !== scope.since
      ? `Since ${formatDateTime(scope.since)}, the scan before it was first seen (${scans}${capped})`
      : `Since the first snapshot ${formatDateTime(scope.since)} (${scans}${capped})`
  }
  if (scope.basis === 'latest_differing_pair') {
    return scope.since && scope.until
      ? `No change log on this database: newest snapshots that differ, ${formatDateTime(scope.since)} to ${formatTime(scope.until)}`
      : 'No change log on this database and no two snapshots differ yet'
  }
  return 'No snapshots to compare yet'
}

function FindingDrawer({ finding, onClose }: { finding: Finding | null; onClose: () => void }) {
  const { connectionId } = useAppState()
  const { openDrawer } = useAssistantDrawer()
  const empty: Related = { changes: [], resources: [], events: [], window: null, scope: null }
  const [related, setRelated] = useState<Related>(empty)

  // Gather related changes, resources and vCenter events for the evidence package (best effort, page still works if it fails).
  useEffect(() => {
    if (!finding) return
    let cancelled = false
    setRelated(empty)
    ;(async () => {
      let window: Related['window'] = null
      let scope: RelatedWindow | null = null
      let events: Event[] = []
      try {
        const [resources, snaps] = await Promise.all([getResources(connectionId), getSnapshots(connectionId)])
        const target = resources.find(r => r.id === finding.resource_id)
        const near = new Set<string>([finding.resource_id ?? ''])
        if (target) {
          if (target.parent_id) near.add(target.parent_id)
          for (const rel of target.relationships) near.add(rel.target_id)
          for (const r of resources) if (r.parent_id === target.id) near.add(r.id)
        }
        const relRes = resources.filter(r => near.has(r.id)).slice(0, 12)
        let changes: Change[] = []
        // Resolve which vCenter this finding belongs to: the selected connection, else the namespaced
        // resource id (host:<connection_id>:host-12), else the resource's source (vcenter:<connection_id>).
        const candidates = [connectionId, finding.resource_id?.split(':')[1], target?.source?.split(':')[1]].filter((c): c is string => !!c)
        const conn = candidates.find(c => snaps.some(s => s.connection_id === c)) ?? null
        // Related changes: the change log since the finding was first observed (issue #5), not just the newest
        // pair of snapshots, so two identical back-to-back scans no longer hide the cause. Best effort.
        if (conn) {
          try {
            const rel = await getFindingRelated(finding.id, conn)
            changes = rel.changes
            scope = rel.window
            if (rel.window.since) window = { since: rel.window.since, until: rel.window.until }
          } catch { changes = [] }
        }
        // Events in the same window as the related changes; with nothing to go on, the last 24 h.
        if (conn) {
          window = window ?? { since: new Date(Date.now() - 86_400_000).toISOString(), until: null }
          try {
            const own = finding.resource_id ? await getEvents({ connectionId: conn, since: window.since, until: window.until, resourceId: finding.resource_id, limit: 10 }) : []
            const rest = own.length < 10 ? await getEvents({ connectionId: conn, since: window.since, until: window.until, limit: 20 }) : []
            const seen = new Set(own.map(e => e.id))
            events = [...own, ...rest.filter(e => !seen.has(e.id))].slice(0, 10)
          } catch { events = [] }
        }
        if (!cancelled) setRelated({ changes, resources: relRes, events, window, scope })
      } catch { if (!cancelled) setRelated({ changes: [], resources: [], events, window, scope }) }
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finding, connectionId])

  const ask = (task: AssistantTask) => {
    if (!finding) return
    const q = task === 'explain' ? `Explain the finding "${finding.title}" and why it matters.`
      : task === 'investigate' ? `How should I investigate "${finding.title}"? What changed around it?`
      : `Generate an investigation script for "${finding.title}".`
    openDrawer({ task, question: q, findings: [finding], changes: related.changes, resources: related.resources, events: related.events, autoSend: true, scriptFormat: 'powercli' })
  }

  return (
    <Drawer open={!!finding} onClose={onClose} width="w-[600px]"
      header={finding ? (
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-1.5"><SeverityBadge severity={finding.severity} /><span className="text-xs text-faint font-mono">{finding.check_id}</span></div>
          <h2 className="text-lg font-semibold tracking-tight leading-snug">{finding.title}</h2>
        </div>
      ) : null}>
      {finding ? (
        <div className="px-5 py-5 space-y-6">
          {finding.resource_name ? (
            <div className="flex items-center gap-2 text-sm">
              <ResourceIcon type={finding.resource_type ?? ''} className="text-muted" />
              <span className="font-medium">{finding.resource_name}</span>
              <span className="text-faint"><ResourceTypeLabel type={finding.resource_type ?? ''} /></span>
            </div>
          ) : null}
          <p className="text-sm leading-relaxed text-fg">{finding.summary}</p>

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-faint mb-2.5">Evidence</h3>
            <EvidenceTable evidence={finding.evidence} />
          </section>

          {finding.recommendation ? (
            <section className="rounded-lg border border-border bg-surface-2 px-4 py-3.5">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-faint mb-1.5 inline-flex items-center gap-1.5"><Lightbulb size={13} /> Recommendation</h3>
              <p className="text-sm leading-relaxed">{finding.recommendation}</p>
            </section>
          ) : null}

          <section>
            <div className="flex items-baseline justify-between gap-3 mb-2.5">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-faint">Related changes</h3>
              {related.scope ? <span className="text-xs text-faint tnum text-right">{describeScope(related.scope)}</span> : null}
            </div>
            {related.changes.length === 0 ? (
              <p className="text-sm text-faint">{related.scope ? 'No recorded changes to this object or its neighbours in this window.' : 'Loading changes'}</p>
            ) : (
              <ul className="space-y-1.5">
                {related.changes.map((c, i) => (
                  <li key={i} className="text-sm flex items-center gap-2"><span className={`h-1.5 w-1.5 rounded-full ${c.significance === 'high' ? 'bg-critical' : c.significance === 'medium' ? 'bg-warning' : 'bg-faint'}`} /><span className="font-medium">{c.resource_name}</span><span className="text-muted">{c.summary}</span></li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <div className="flex items-baseline justify-between gap-3 mb-2.5">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-faint">Events in this window</h3>
              {related.window ? <span className="text-xs text-faint tnum">{formatDateTime(related.window.since)} to {related.window.until ? formatTime(related.window.until) : 'now'}</span> : null}
            </div>
            {related.events.length === 0 ? <p className="text-sm text-faint">{related.window ? 'No vCenter events recorded in this window.' : 'Loading events'}</p> : (
              <ul className="divide-y divide-border rounded-lg border border-border overflow-hidden">
                {related.events.map(e => (
                  <li key={e.id} className="px-3.5 py-2 flex items-start gap-2.5 bg-surface">
                    <EventCategoryDot category={e.category} className="mt-[7px]" />
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] leading-snug">{e.message}</p>
                      <p className="text-xs text-faint mt-0.5 flex items-center gap-2 min-w-0">
                        <span className="tnum" title={formatDateTime(e.time)}>{formatTime(e.time)} ({relativeTime(e.time)})</span>
                        {e.user ? <span className="truncate">{e.user}</span> : null}
                        {e.resource_name && e.resource_id !== finding.resource_id ? <span className="truncate">{e.resource_name}</span> : null}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="pt-2 border-t border-border">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-faint">Assistant</h3>
              <span className="text-xs text-faint">Using: 1 finding, {related.changes.length} {related.changes.length === 1 ? 'change' : 'changes'}, {related.resources.length} {related.resources.length === 1 ? 'resource' : 'resources'}, {related.events.length} {related.events.length === 1 ? 'event' : 'events'}</span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <Button onClick={() => ask('explain')}><Sparkles size={14} /> Explain</Button>
              <Button onClick={() => ask('investigate')}><Search size={14} /> Investigate</Button>
              <Button onClick={() => ask('generate-script')}><Terminal size={14} /> Generate Script</Button>
            </div>
          </section>
        </div>
      ) : null}
    </Drawer>
  )
}
