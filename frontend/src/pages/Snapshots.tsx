import { useMemo, useState } from 'react'
import { Camera, Search, Trash2 } from 'lucide-react'
import type { SnapshotSummary, SnapshotTier } from '@/types'
import { createSnapshot, deleteSnapshot, getSnapshots } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { useAppState } from '@/state/AppState'
import { Badge, Button, Card, Dialog, EmptyState, ErrorState, Field, Input, PageHeader, Select, Skeleton } from '@/components/ui'
import { TierBadge, snapshotTier, tierLabel } from '@/components/domain'
import { formatDateTime, formatTime, groupByDay, relativeTime } from '@/lib/format'

const TIER_ORDER: SnapshotTier[] = ['manual', 'recent', 'hourly', 'daily']

export default function SnapshotsPage() {
  const { connectionId, connections, refreshKey, refreshAll } = useAppState()
  const snaps = useAsync(() => getSnapshots(connectionId), [connectionId, refreshKey])
  const [open, setOpen] = useState(false)
  const [label, setLabel] = useState('')
  const [target, setTarget] = useState(connectionId ?? connections[0]?.id ?? '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const connName = (id: string) => connections.find(c => c.id === id)?.name ?? id

  const capture = async () => {
    const conn = connectionId ?? target
    if (!conn) return
    setBusy(true); setErr(null)
    try { await createSnapshot(conn, label.trim() || 'Manual'); setOpen(false); setLabel(''); snaps.reload(); refreshAll() }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  const remove = async (id: string) => {
    setBusy(true)
    try { await deleteSnapshot(id); setConfirm(null); snaps.reload(); refreshAll() }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  const list = useMemo(() => (snaps.data ?? []).slice().sort((a, b) => b.created_at.localeCompare(a.created_at)), [snaps.data])
  const latestId = list[0]?.id
  const tierCounts = useMemo(() => {
    const c: Record<SnapshotTier, number> = { manual: 0, recent: 0, hourly: 0, daily: 0 }
    for (const s of list) c[snapshotTier(s)]++
    return c
  }, [list])

  // Text filter matches label, tier, connection name and the formatted date/time.
  const needle = q.trim().toLowerCase()
  const filtered = useMemo(() => !needle ? list : list.filter(s =>
    [s.label, tierLabel[snapshotTier(s)], connName(s.connection_id), formatDateTime(s.created_at), relativeTime(s.created_at)]
      .some(t => t.toLowerCase().includes(needle))), [list, needle]) // eslint-disable-line react-hooks/exhaustive-deps
  const groups = useMemo(() => groupByDay(filtered, s => s.created_at), [filtered])

  return (
    <div className="anim-fade-up">
      <PageHeader title="Snapshots" subtitle="Point-in-time inventory captures. Scheduled ones thin out with age; manual ones stay until you delete them."
        actions={<>
          <div className="relative"><Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" /><Input value={q} onChange={e => setQ(e.target.value)} placeholder="Filter by label, tier or date" className="pl-8 w-64" aria-label="Filter snapshots" /></div>
          <Button variant="primary" onClick={() => { setTarget(connectionId ?? connections[0]?.id ?? ''); setOpen(true) }} disabled={connections.length === 0}><Camera size={15} /> Capture Snapshot</Button>
        </>} />

      {snaps.error && !snaps.data ? <Card><ErrorState title="Snapshots unavailable" error={snaps.error} onRetry={snaps.reload} /></Card>
        : snaps.loading && !snaps.data ? <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map(i => <Skeleton key={i} className="h-36 rounded-xl" />)}</div>
        : list.length === 0 ? (
          <Card><EmptyState icon={<Camera size={20} />} title="No snapshots yet" body="The first scheduled scan creates one automatically. You can also capture one now."
            action={connections.length > 0 ? <Button variant="primary" onClick={() => setOpen(true)}>Capture Snapshot</Button> : undefined} /></Card>
        ) : (
          <>
            <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2 mb-5 text-sm">
              <span className="text-2xl font-semibold tracking-tight tnum">{list.length} <span className="text-base text-muted font-medium">{list.length === 1 ? 'snapshot' : 'snapshots'}</span></span>
              {TIER_ORDER.filter(t => tierCounts[t] > 0).map(t => <span key={t} className="text-muted"><span className="font-semibold text-fg tnum">{tierCounts[t]}</span> {tierLabel[t].toLowerCase()}</span>)}
              {needle ? <span className="text-muted ml-auto">{filtered.length} match{filtered.length === 1 ? '' : 'es'} <button className="text-accent hover:underline ml-1" onClick={() => setQ('')}>clear</button></span> : null}
            </div>
            {filtered.length === 0 ? (
              <Card><EmptyState icon={<Search size={20} />} title="No snapshots match" body={`Nothing matches "${q}".`} action={<Button onClick={() => setQ('')}>Clear filter</Button>} /></Card>
            ) : (
              <div className="space-y-8">
                {groups.map(g => (
                  <section key={g.key}>
                    <h2 className="flex items-baseline gap-2 text-xs font-semibold uppercase tracking-wider text-faint mb-3 sticky top-0 bg-bg/95 backdrop-blur py-1 z-10">
                      {g.label} <span className="tnum font-medium normal-case tracking-normal">{g.items.length}</span>
                    </h2>
                    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                      {g.items.map(s => (
                        <SnapshotCard key={s.id} s={s} latest={s.id === latestId} showConnection={!connectionId} connName={connName} onDelete={() => setConfirm(s.id)} />
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </>
        )}

      <Dialog open={open} onClose={() => setOpen(false)} title="Capture Snapshot"
        footer={<><Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" onClick={() => void capture()} loading={busy} disabled={!(connectionId ?? target)}>Capture</Button></>}>
        <div className="space-y-4">
          <p className="text-sm text-muted">Collects the inventory right now and stores it as a named point in time you can compare against later. Manual snapshots are never pruned.</p>
          {!connectionId ? (
            <Field label="Connection">
              <Select value={target} onChange={e => setTarget(e.target.value)} className="w-full">
                {connections.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </Select>
            </Field>
          ) : null}
          <Field label="Label" hint="For example: Before firmware window">
            <Input autoFocus value={label} onChange={e => setLabel(e.target.value)} placeholder="Manual" onKeyDown={e => { if (e.key === 'Enter') void capture() }} />
          </Field>
          {err ? <p className="text-sm text-critical">{err}</p> : null}
        </div>
      </Dialog>

      <Dialog open={!!confirm} onClose={() => setConfirm(null)} title="Delete snapshot"
        footer={<><Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button><Button variant="danger" onClick={() => confirm && void remove(confirm)} loading={busy}>Delete</Button></>}>
        <p className="text-sm">This removes the snapshot permanently. Comparisons that used it will no longer be available.</p>
        {err ? <p className="text-sm text-critical mt-3">{err}</p> : null}
      </Dialog>
    </div>
  )
}

function SnapshotCard({ s, latest, showConnection, connName, onDelete }: { s: SnapshotSummary; latest: boolean; showConnection: boolean; connName: (id: string) => string; onDelete: () => void }) {
  return (
    <Card className="px-5 py-4 flex flex-col gap-3 anim-fade-up">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-[15px] font-semibold tracking-tight truncate">{s.label}</h3>
            {latest ? <Badge tone="accent">Latest</Badge> : null}
          </div>
          <p className="text-sm text-muted mt-0.5"><span className="tnum">{formatTime(s.created_at)}</span> <span className="text-faint">({relativeTime(s.created_at)})</span></p>
        </div>
        <TierBadge snapshot={s} />
      </div>
      <div className="flex items-center justify-between text-sm pt-2 border-t border-border">
        <div className="text-muted"><span className="text-fg font-semibold tnum">{s.resource_count}</span> resources{showConnection ? <span className="text-faint"> in {connName(s.connection_id)}</span> : null}</div>
        <button onClick={onDelete} className="text-faint hover:text-critical transition-colors p-1 rounded" title="Delete snapshot"><Trash2 size={15} /></button>
      </div>
    </Card>
  )
}
