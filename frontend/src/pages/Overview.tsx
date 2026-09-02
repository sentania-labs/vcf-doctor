import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, Boxes, HardDrive, MonitorSmartphone, Plug, Server } from 'lucide-react'
import { getChangesMinSignificance, getEvents, getOverview } from '@/api'
import type { Event } from '@/types'
import { useAsync } from '@/hooks/useAsync'
import { useAppState } from '@/state/AppState'
import { Button, Card, CardHeader, EmptyState, ErrorState, Skeleton } from '@/components/ui'
import { ChangeRow, EventCategoryDot, FindingRow, StatCard } from '@/components/domain'
import { formatDateTime, pct, relativeTime } from '@/lib/format'
import { cn } from '@/lib/cn'

function HealthRing({ score }: { score: number }) {
  const r = 54, c = 2 * Math.PI * r
  const tone = score >= 90 ? 'var(--ok)' : score >= 70 ? 'var(--warning)' : 'var(--critical)'
  return (
    <div className="relative h-32 w-32 shrink-0">
      <svg viewBox="0 0 128 128" className="h-32 w-32 -rotate-90">
        <circle cx="64" cy="64" r={r} fill="none" stroke="var(--surface-3)" strokeWidth="9" />
        <circle cx="64" cy="64" r={r} fill="none" stroke={tone} strokeWidth="9" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - Math.max(0, Math.min(100, score)) / 100)} style={{ transition: 'stroke-dashoffset 700ms cubic-bezier(.2,.8,.2,1)' }} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-4xl font-semibold tracking-tight tnum">{Math.round(score)}</span>
        <span className="text-[11px] uppercase tracking-wider text-faint font-semibold">of 100</span>
      </div>
    </div>
  )
}

export default function OverviewPage() {
  const { connectionId, refreshKey, connections, connectionsLoading, selected, backend } = useAppState()
  // Recent changes honour the Settings significance floor; a settings failure falls back to everything.
  const ov = useAsync(() => getChangesMinSignificance().then(min => getOverview(connectionId, min)), [connectionId, refreshKey])
  // Notable vCenter events from the last day (user actions, warnings, errors). Best effort: the card hides on failure.
  const ev = useAsync<Event[] | null>(() => getEvents({ connectionId, limit: 60 }).then(list => list.filter(e => e.category !== 'info').slice(0, 5)).catch(() => null), [connectionId, refreshKey])
  const nav = useNavigate()

  if (!connectionsLoading && connections.length === 0 && !ov.data && !ov.error && backend !== 'down') {
    return (
      <Card>
        <EmptyState icon={<Plug size={20} />} title="No vCenter connected yet"
          body="Add a vCenter connection with a scan interval. VCF Doctor captures the first snapshot on its own and this page fills in."
          action={<Button variant="primary" onClick={() => nav('/connections')}>Add vCenter</Button>} />
      </Card>
    )
  }
  if (ov.error && !ov.data) return <Card><ErrorState title="Overview unavailable" error={ov.error} onRetry={ov.reload} /></Card>

  const d = ov.data
  const scope = selected ? selected.name : 'All connections'
  const verdict = !d ? '' : d.counts.critical > 0 ? `${d.counts.critical} critical ${d.counts.critical === 1 ? 'issue needs' : 'issues need'} attention` : d.counts.warning > 0 ? `${d.counts.warning} ${d.counts.warning === 1 ? 'warning' : 'warnings'}, nothing critical` : 'Environment healthy'
  const verdictTone = !d ? '' : d.counts.critical > 0 ? 'text-critical' : d.counts.warning > 0 ? 'text-warning' : 'text-ok'

  return (
    <div className="space-y-6 anim-fade-up">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Environment Health</h1>
          <p className="text-sm text-muted mt-1">{scope}{d?.last_scan ? `. Scanned ${relativeTime(d.last_scan)}` : ''}</p>
        </div>
      </div>

      {/* Hero */}
      {/* Below 2xl the hero takes its own row so the verdict and counts never collide with the stat cards. */}
      <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4 2xl:grid-cols-[minmax(0,2fr)_repeat(4,minmax(0,1fr))]">
        <Card className="px-6 py-5 flex items-center gap-8 sm:col-span-2 lg:col-span-4 2xl:col-span-1 2xl:min-w-[360px]">
          {d ? <HealthRing score={d.health_score} /> : <Skeleton className="h-32 w-32 rounded-full" />}
          <div className="min-w-0">
            {d ? <>
              <p className={cn('text-xl font-semibold tracking-tight leading-snug', verdictTone)}>{verdict}</p>
              <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm">
                <Row dot="bg-ok" label="passed" n={d.counts.passed} />
                <Row dot="bg-critical" label="critical" n={d.counts.critical} />
                <Row dot="bg-warning" label={d.counts.warning === 1 ? 'warning' : 'warnings'} n={d.counts.warning} />
                <Row dot="bg-info" label="info" n={d.counts.info} />
              </dl>
            </> : <div className="space-y-2"><Skeleton className="h-6 w-56" /><Skeleton className="h-4 w-40" /><Skeleton className="h-4 w-32" /></div>}
          </div>
        </Card>
        {d ? <>
          <StatCard label="Resources" value={d.resources.total} icon={<Boxes size={16} />} sub={Object.entries(d.resources.by_type).filter(([k]) => ['host', 'vm', 'datastore'].includes(k)).map(([k, v]) => `${v} ${k}${v === 1 ? '' : 's'}`).join(', ')} />
          <StatCard label="Hosts" value={<>{d.hosts_connected}<span className="text-muted text-xl"> / {d.hosts_total}</span></>} icon={<Server size={16} />} sub="connected" tone={d.hosts_connected < d.hosts_total ? 'critical' : undefined} />
          <StatCard label="VMs" value={d.vms_on} icon={<MonitorSmartphone size={16} />} sub={`powered on of ${d.vms_total}`} />
          <StatCard label="Storage" value={pct(d.storage_free_pct)} icon={<HardDrive size={16} />} sub="available" tone={d.storage_free_pct !== null && d.storage_free_pct < 15 ? 'warning' : undefined} />
        </> : [0, 1, 2, 3].map(i => <Skeleton key={i} className="h-[124px] rounded-xl" />)}
      </div>

      <div className="grid gap-5 xl:grid-cols-2 2xl:grid-cols-3">
        <Card>
          <CardHeader title="Important findings" subtitle="Highest severity first"
            action={<Link to="/health" className="text-sm text-accent hover:underline inline-flex items-center gap-1 whitespace-nowrap shrink-0">All findings <ArrowRight size={14} /></Link>} />
          <div className="px-5 pb-5 space-y-2">
            {!d ? [0, 1, 2].map(i => <Skeleton key={i} className="h-16" />)
              : d.top_findings.length === 0 ? <EmptyState title="No findings" body="Every diagnostic check passed on the last scan." />
              : d.top_findings.map(f => <FindingRow key={f.id} finding={f} compact onClick={() => nav(`/health?finding=${encodeURIComponent(f.id)}`)} />)}
          </div>
        </Card>
        <Card>
          <CardHeader title="Recent changes" subtitle="Since the previous snapshot, at the significance set in Settings"
            action={<Link to="/changes" className="text-sm text-accent hover:underline inline-flex items-center gap-1 whitespace-nowrap shrink-0">Time machine <ArrowRight size={14} /></Link>} />
          <div className="px-5 pb-5 space-y-2">
            {!d ? [0, 1, 2].map(i => <Skeleton key={i} className="h-16" />)
              : d.recent_changes.length === 0 ? <EmptyState title="No changes" body="Nothing has changed between the last two snapshots." />
              : d.recent_changes.map((c, i) => <ChangeRow key={`${c.resource_id}-${i}`} change={c} compact />)}
          </div>
        </Card>
        {ev.data !== null || ev.loading ? (
          <Card className="xl:col-span-2 2xl:col-span-1">
            <CardHeader title="Recent events" subtitle="User actions, warnings and errors vCenter recorded in the last day"
              action={<Link to="/events" className="text-sm text-accent hover:underline inline-flex items-center gap-1 whitespace-nowrap shrink-0">All events <ArrowRight size={14} /></Link>} />
            <div className="px-5 pb-5">
              {!ev.data ? <div className="space-y-2">{[0, 1, 2].map(i => <Skeleton key={i} className="h-10" />)}</div>
                : ev.data.length === 0 ? <EmptyState title="Quiet day" body="No user actions, warnings or errors in the last 24 hours." />
                : (
                  <ul className="divide-y divide-border rounded-lg border border-border overflow-hidden">
                    {ev.data.map(e => (
                      <li key={e.id} className="px-3.5 py-2.5 flex items-start gap-2.5 bg-surface">
                        <EventCategoryDot category={e.category} className="mt-[7px]" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm leading-snug line-clamp-2">{e.message}</p>
                          <p className="text-xs text-faint mt-0.5 flex items-center gap-2 min-w-0">
                            <span className="tnum" title={formatDateTime(e.time)}>{relativeTime(e.time)}</span>
                            {e.user ? <span className="truncate">{e.user}</span> : null}
                            {e.resource_name ? <span className="truncate">{e.resource_name}</span> : null}
                          </p>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
            </div>
          </Card>
        ) : null}
      </div>
    </div>
  )
}

function Row({ dot, label, n }: { dot: string; label: string; n: number }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className={cn('h-2 w-2 rounded-full', dot)} />
      <span className="font-semibold tnum w-8">{n}</span>
      <span className="text-muted">{label}</span>
    </div>
  )
}
