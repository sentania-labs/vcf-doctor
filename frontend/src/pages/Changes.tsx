import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, ArrowDown, Camera, GitCompareArrows } from 'lucide-react'
import type { Change, Significance } from '@/types'
import { getChanges, getSnapshots } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { useAppState } from '@/state/AppState'
import { Button, Card, EmptyState, ErrorState, PageHeader, Select, Skeleton } from '@/components/ui'
import { ChangeRow, significanceLabel } from '@/components/domain'
import { formatDateTime, relativeTime } from '@/lib/format'
import { cn } from '@/lib/cn'

export default function ChangesPage() {
  const { connectionId, connections, setSelectedId, refreshKey } = useAppState()
  const nav = useNavigate()
  const snaps = useAsync(() => getSnapshots(connectionId), [connectionId, refreshKey], !!connectionId)
  const sorted = useMemo(() => (snaps.data ?? []).slice().sort((a, b) => b.created_at.localeCompare(a.created_at)), [snaps.data])
  const [from, setFrom] = useState<string>('')
  const [to, setTo] = useState<string>('')

  // Default: TO = newest, FROM = the one before it. Keep choices when still valid.
  useEffect(() => {
    if (sorted.length === 0) return
    const ids = new Set(sorted.map(s => s.id))
    if (!ids.has(to)) setTo(sorted[0].id)
    if (!ids.has(from)) setFrom(sorted[1]?.id ?? sorted[0].id)
  }, [sorted, from, to])

  // Always compare oldest to newest. If the user picked FROM newer than TO, swap for the query and say so.
  const pickedFrom = sorted.find(s => s.id === from)
  const pickedTo = sorted.find(s => s.id === to)
  const swapped = !!pickedFrom && !!pickedTo && pickedFrom.created_at > pickedTo.created_at
  const queryFrom = swapped ? to : from
  const queryTo = swapped ? from : to
  const changes = useAsync(() => getChanges(connectionId as string, queryFrom, queryTo), [connectionId, queryFrom, queryTo], !!connectionId && !!queryFrom && !!queryTo)

  if (!connectionId) {
    return (
      <div className="anim-fade-up">
        <PageHeader title="What Changed?" subtitle="Compare two snapshots of one vCenter" />
        <Card>
          <EmptyState icon={<GitCompareArrows size={20} />} title="Pick a connection"
            body="Snapshots are per vCenter, so the time machine compares two snapshots of the same connection. Choose one to continue."
            action={connections.length > 0 ? (
              <div className="flex flex-wrap gap-2 justify-center">
                {connections.map(c => <Button key={c.id} onClick={() => setSelectedId(c.id)}>{c.name}</Button>)}
              </div>
            ) : <Button variant="primary" onClick={() => nav('/connections')}>Add vCenter</Button>} />
        </Card>
      </div>
    )
  }

  const groups: Record<Significance, Change[]> = { high: [], medium: [], low: [] }
  for (const c of changes.data ?? []) groups[c.significance].push(c)
  const total = changes.data?.length ?? 0
  const fromSnap = swapped ? pickedTo : pickedFrom
  const toSnap = swapped ? pickedFrom : pickedTo

  return (
    <div className="anim-fade-up">
      <PageHeader title="What Changed?" subtitle="Semantic differences between two snapshots, most important first" />

      <Card className="px-5 py-4 mb-6">
        {snaps.loading && !snaps.data ? <Skeleton className="h-9 w-full" />
          : snaps.error ? <ErrorState title="Snapshots unavailable" error={snaps.error} onRetry={snaps.reload} />
          : sorted.length < 2 ? (
            <EmptyState icon={<Camera size={20} />} title={sorted.length === 0 ? 'No snapshots yet' : 'Only one snapshot so far'}
              body="Two snapshots are needed to compare. Run Scan Now or wait for the next scheduled scan."
              action={<Button onClick={() => nav('/snapshots')}>Go to Snapshots</Button>} />
          ) : (
            <div className="grid gap-4 md:grid-cols-[1fr_auto_1fr] items-end">
              <SnapPicker label="From" value={from} onChange={setFrom} snaps={sorted} />
              <div className="hidden md:flex items-center justify-center pb-2 text-faint"><ArrowDown size={18} className="-rotate-90" /></div>
              <SnapPicker label="To" value={to} onChange={setTo} snaps={sorted} />
            </div>
          )}
        {swapped ? (
          <p role="status" className="mt-3 flex items-center gap-1.5 text-xs text-warning"><AlertTriangle size={13} /> FROM was newer than TO; snapshots swapped so changes read oldest to newest.</p>
        ) : null}
      </Card>

      {sorted.length >= 2 ? (
        changes.error ? <Card><ErrorState title="Could not compute changes" error={changes.error} onRetry={changes.reload} /></Card>
        : changes.loading && !changes.data ? <div className="space-y-3">{[0, 1, 2].map(i => <Skeleton key={i} className="h-28" />)}</div>
        : (
          <>
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2 mb-6">
              <span className="text-3xl font-semibold tracking-tight tnum">{total} <span className="text-lg text-muted font-medium">{total === 1 ? 'change' : 'changes'}</span></span>
              <Summary n={groups.high.length} label="high impact" dot="bg-critical" />
              <Summary n={groups.medium.length} label="medium" dot="bg-warning" />
              <Summary n={groups.low.length} label="informational" dot="bg-faint" />
              {fromSnap && toSnap ? <span className="text-sm text-muted ml-auto">{formatDateTime(fromSnap.created_at)} to {formatDateTime(toSnap.created_at)}</span> : null}
            </div>
            {total === 0 ? (
              <Card><EmptyState icon={<GitCompareArrows size={20} />} title="No differences" body={from === to ? 'FROM and TO are the same snapshot.' : 'These two snapshots describe the same environment state.'} /></Card>
            ) : (
              <div className="space-y-8">
                {(['high', 'medium', 'low'] as Significance[]).map(sig => groups[sig].length === 0 ? null : (
                  <section key={sig}>
                    <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-faint mb-3">
                      <span className={cn('h-2 w-2 rounded-full', sig === 'high' ? 'bg-critical' : sig === 'medium' ? 'bg-warning' : 'bg-faint')} />
                      {significanceLabel[sig]} <span className="tnum">{groups[sig].length}</span>
                    </h2>
                    <div className="grid gap-3 lg:grid-cols-2">
                      {groups[sig].map((c, i) => <ChangeRow key={`${c.resource_id}-${i}`} change={c} />)}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </>
        )
      ) : null}
    </div>
  )
}

function SnapPicker({ label, value, onChange, snaps }: { label: string; value: string; onChange: (v: string) => void; snaps: Array<{ id: string; label: string; created_at: string; scheduled: boolean }> }) {
  const s = snaps.find(x => x.id === value)
  return (
    <label className="block">
      <span className="block text-[11px] font-semibold uppercase tracking-wider text-faint mb-1.5">{label}</span>
      <Select value={value} onChange={e => onChange(e.target.value)} className="w-full h-10">
        {snaps.map(x => <option key={x.id} value={x.id}>{x.label} ({formatDateTime(x.created_at)})</option>)}
      </Select>
      {s ? <span className="block text-xs text-muted mt-1.5">{relativeTime(s.created_at)}, {s.scheduled ? 'scheduled' : 'manual'}</span> : null}
    </label>
  )
}

function Summary({ n, label, dot }: { n: number; label: string; dot: string }) {
  return <span className="inline-flex items-center gap-2 text-sm"><span className={cn('h-2 w-2 rounded-full', dot)} /><span className="font-semibold tnum">{n}</span><span className="text-muted">{label}</span></span>
}
