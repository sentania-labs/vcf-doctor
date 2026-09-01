import { useState } from 'react'
import { Camera, Clock, Hand, Trash2 } from 'lucide-react'
import { createSnapshot, deleteSnapshot, getSnapshots } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { useAppState } from '@/state/AppState'
import { Badge, Button, Card, Dialog, EmptyState, ErrorState, Field, Input, PageHeader, Select, Skeleton } from '@/components/ui'
import { formatDateTime, relativeTime } from '@/lib/format'

export default function SnapshotsPage() {
  const { connectionId, connections, refreshKey, refreshAll } = useAppState()
  const snaps = useAsync(() => getSnapshots(connectionId), [connectionId, refreshKey])
  const [open, setOpen] = useState(false)
  const [label, setLabel] = useState('')
  const [target, setTarget] = useState(connectionId ?? connections[0]?.id ?? '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<string | null>(null)
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

  const list = (snaps.data ?? []).slice().sort((a, b) => b.created_at.localeCompare(a.created_at))

  return (
    <div className="anim-fade-up">
      <PageHeader title="Snapshots" subtitle="Point-in-time inventory captures, scheduled and manual"
        actions={<Button variant="primary" onClick={() => { setTarget(connectionId ?? connections[0]?.id ?? ''); setOpen(true) }} disabled={connections.length === 0}><Camera size={15} /> Capture Snapshot</Button>} />

      {snaps.error && !snaps.data ? <Card><ErrorState title="Snapshots unavailable" error={snaps.error} onRetry={snaps.reload} /></Card>
        : snaps.loading && !snaps.data ? <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map(i => <Skeleton key={i} className="h-36 rounded-xl" />)}</div>
        : list.length === 0 ? (
          <Card><EmptyState icon={<Camera size={20} />} title="No snapshots yet" body="The first scheduled scan creates one automatically. You can also capture one now."
            action={connections.length > 0 ? <Button variant="primary" onClick={() => setOpen(true)}>Capture Snapshot</Button> : undefined} /></Card>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {list.map((s, i) => (
              <Card key={s.id} className="px-5 py-4 flex flex-col gap-3 anim-fade-up" >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="text-[15px] font-semibold tracking-tight truncate">{s.label}</h3>
                      {i === 0 ? <Badge tone="accent">Latest</Badge> : null}
                    </div>
                    <p className="text-sm text-muted mt-0.5">{formatDateTime(s.created_at)} <span className="text-faint">({relativeTime(s.created_at)})</span></p>
                  </div>
                  <Badge tone={s.scheduled ? 'neutral' : 'info'}>{s.scheduled ? <><Clock size={11} /> Scheduled</> : <><Hand size={11} /> Manual</>}</Badge>
                </div>
                <div className="flex items-center justify-between text-sm pt-2 border-t border-border">
                  <div className="text-muted"><span className="text-fg font-semibold tnum">{s.resource_count}</span> resources{!connectionId ? <span className="text-faint"> in {connName(s.connection_id)}</span> : null}</div>
                  <button onClick={() => setConfirm(s.id)} className="text-faint hover:text-critical transition-colors p-1 rounded" title="Delete snapshot"><Trash2 size={15} /></button>
                </div>
              </Card>
            ))}
          </div>
        )}

      <Dialog open={open} onClose={() => setOpen(false)} title="Capture Snapshot"
        footer={<><Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" onClick={() => void capture()} loading={busy} disabled={!(connectionId ?? target)}>Capture</Button></>}>
        <div className="space-y-4">
          <p className="text-sm text-muted">Collects the inventory right now and stores it as a named point in time you can compare against later.</p>
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
