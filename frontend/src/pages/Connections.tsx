import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, FlaskConical, KeyRound, Plug, Plus, Trash2, XCircle } from 'lucide-react'
import type { ConnectionCreate, ConnectionPublic, ConnectionTestResult, Schedule } from '@/types'
import { createConnection, deleteConnection, getSchedule, testConnection, testConnectionDraft, updateConnection, updateSchedule } from '@/api'
import { ApiError } from '@/api/client'
import { useAppState } from '@/state/AppState'
import { Badge, Button, Card, Dialog, EmptyState, Field, Input, PageHeader, Select, Skeleton, Toggle } from '@/components/ui'
import { formatDateTime, relativeTime } from '@/lib/format'

const INTERVALS = [5, 10, 15, 30, 60, 240, 1440]
const intervalLabel = (m: number) => (m >= 1440 ? `${m / 1440} day` : m >= 60 ? `${m / 60} hour${m / 60 === 1 ? '' : 's'}` : `${m} min`)

export default function ConnectionsPage() {
  const { connections, connectionsLoading, reloadConnections, refreshAll } = useAppState()
  const [adding, setAdding] = useState(false)
  const [confirm, setConfirm] = useState<ConnectionPublic | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const remove = async (c: ConnectionPublic) => {
    setBusy(true); setErr(null)
    try { await deleteConnection(c.id); setConfirm(null); await reloadConnections(); refreshAll() }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="anim-fade-up">
      <PageHeader title="Connections" subtitle="Each vCenter scans on its own schedule and keeps its own snapshots"
        actions={<Button variant="primary" onClick={() => setAdding(true)}><Plus size={15} /> Add vCenter</Button>} />

      {connectionsLoading ? <div className="space-y-4">{[0, 1].map(i => <Skeleton key={i} className="h-32 rounded-xl" />)}</div>
        : connections.length === 0 ? (
          <Card><EmptyState icon={<Plug size={20} />} title="No connections" body="Add a vCenter with a scan interval. The first snapshot is captured automatically and every page fills in from there."
            action={<Button variant="primary" onClick={() => setAdding(true)}>Add vCenter</Button>} /></Card>
        ) : (
          <div className="space-y-4">
            {connections.map(c => <ConnectionCard key={c.id} c={c} onDelete={() => setConfirm(c)} />)}
            <Card className="px-5 py-4 flex items-center gap-4 opacity-70">
              <div className="h-9 w-9 rounded-lg bg-surface-2 border border-border flex items-center justify-center text-muted"><FlaskConical size={16} /></div>
              <div className="flex-1">
                <div className="flex items-center gap-2"><span className="font-semibold text-[15px]">SDDC Manager</span><Badge tone="neutral">Experimental</Badge></div>
                <p className="text-sm text-muted mt-0.5">Discover workload domains and their vCenters from SDDC Manager. Not available in this release.</p>
              </div>
              <Button disabled>Add SDDC Manager</Button>
            </Card>
          </div>
        )}

      <AddDialog open={adding} onClose={() => setAdding(false)} onCreated={async () => { setAdding(false); await reloadConnections(); refreshAll() }} />

      <Dialog open={!!confirm} onClose={() => setConfirm(null)} title="Remove connection"
        footer={<><Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button><Button variant="danger" loading={busy} onClick={() => confirm && void remove(confirm)}>Remove</Button></>}>
        <p className="text-sm">Remove <span className="font-semibold">{confirm?.name}</span> and stop its schedule. Existing snapshots for this connection are deleted too.</p>
        {err ? <p className="text-sm text-critical mt-3">{err}</p> : null}
      </Dialog>
    </div>
  )
}

function ConnectionCard({ c, onDelete }: { c: ConnectionPublic; onDelete: () => void }) {
  const [sched, setSched] = useState<Schedule | null>(null)
  const [schedErr, setSchedErr] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [test, setTest] = useState<ConnectionTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const { refreshKey, reloadConnections } = useAppState()
  // Password re-entry: opened automatically when the stored password is unreadable.
  const [pwOpen, setPwOpen] = useState(!!c.needs_credentials)
  const [pw, setPw] = useState('')
  const [pwBusy, setPwBusy] = useState(false)
  const [pwMsg, setPwMsg] = useState<{ ok: boolean; text: string } | null>(null)
  useEffect(() => { if (c.needs_credentials) setPwOpen(true) }, [c.needs_credentials])
  const savePassword = async () => {
    if (!pw) return
    setPwBusy(true); setPwMsg(null)
    try { await updateConnection(c.id, { password: pw }); setPw(''); setPwOpen(false); setPwMsg({ ok: true, text: 'Password updated.' }); await reloadConnections() }
    catch (e) { setPwMsg({ ok: false, text: e instanceof Error ? e.message : String(e) }) }
    finally { setPwBusy(false) }
  }

  useEffect(() => { getSchedule(c.id).then(s => { setSched(s); setSchedErr(null) }).catch(e => setSchedErr(e instanceof Error ? e.message : String(e))) }, [c.id, refreshKey])

  const save = async (patch: Partial<Pick<Schedule, 'interval_minutes' | 'enabled'>>) => {
    if (!sched) return
    const next = { interval_minutes: patch.interval_minutes ?? sched.interval_minutes, enabled: patch.enabled ?? sched.enabled }
    setSaving(true)
    try { setSched(await updateSchedule(c.id, next)) } catch (e) { setSchedErr(e instanceof Error ? e.message : String(e)) } finally { setSaving(false) }
  }
  const runTest = async () => {
    setTesting(true); setTest(null)
    try { setTest(await testConnection(c.id)) } catch (e) { setTest({ ok: false, message: e instanceof Error ? e.message : String(e) }) } finally { setTesting(false) }
  }
  const statusTone = sched?.last_status === 'ok' ? 'ok' : sched?.last_status === 'error' ? 'critical' : sched?.last_status === 'running' ? 'warning' : 'neutral'

  return (
    <Card className="px-5 py-4">
      <div className="flex items-start gap-4 flex-wrap">
        <div className="h-9 w-9 rounded-lg bg-info-bg text-accent flex items-center justify-center shrink-0"><Plug size={16} /></div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="font-semibold text-[15px] tracking-tight">{c.name}</h3>
            <Badge tone="neutral">{c.kind || 'vcenter'}</Badge>
            {sched ? <Badge tone={sched.enabled ? 'ok' : 'neutral'}>{sched.enabled ? 'Scheduled' : 'Paused'}</Badge> : null}
            {c.needs_credentials ? <Badge tone="critical"><AlertTriangle size={11} /> Needs password</Badge> : null}
          </div>
          <p className="text-sm text-muted mt-0.5 font-mono">{c.username} @ {c.host}{c.verify_tls ? '' : '  (TLS verify off)'}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => { setPwOpen(o => !o); setPwMsg(null) }} title="Update the stored password"><KeyRound size={13} /> Password</Button>
          <Button size="sm" onClick={() => void runTest()} loading={testing} disabled={!!c.needs_credentials}>Test</Button>
          <button onClick={onDelete} className="text-faint hover:text-critical transition-colors p-1.5 rounded" title="Remove connection"><Trash2 size={15} /></button>
        </div>
      </div>
      {c.needs_credentials ? (
        <p className="mt-3 text-sm text-critical bg-critical-bg rounded-md px-3 py-2" role="alert">The stored password was encrypted with a different key and cannot be read (the encryption key was lost or rotated). Scans fail until it is entered again; nothing else about this connection is affected.</p>
      ) : null}
      {pwOpen ? (
        <form className="mt-3 flex items-end gap-3 flex-wrap" onSubmit={e => { e.preventDefault(); void savePassword() }}>
          <div className="flex-1 min-w-[220px]"><Field label={`Password for ${c.username}`}><Input type="password" value={pw} onChange={e => setPw(e.target.value)} autoComplete="new-password" disabled={pwBusy} autoFocus /></Field></div>
          <Button type="submit" variant="primary" size="sm" loading={pwBusy} disabled={!pw}>Save password</Button>
          {!c.needs_credentials ? <Button type="button" variant="ghost" size="sm" onClick={() => { setPwOpen(false); setPw('') }}>Cancel</Button> : null}
        </form>
      ) : null}
      {pwMsg ? <p className={`mt-2 text-sm inline-flex items-center gap-1.5 ${pwMsg.ok ? 'text-ok' : 'text-critical'}`}>{pwMsg.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}{pwMsg.text}</p> : null}
      {test ? (
        <p className={`mt-3 text-sm inline-flex items-center gap-1.5 ${test.ok ? 'text-ok' : 'text-critical'}`}>
          {test.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}{test.message}{test.version ? <span className="text-muted">vCenter {test.version}{test.build ? ` build ${test.build}` : ''}</span> : null}
        </p>
      ) : null}

      <div className="mt-4 pt-4 border-t border-border grid gap-4 sm:grid-cols-2 lg:grid-cols-5 items-center">
        {sched ? <>
          <div>
            <div className="text-[11px] uppercase tracking-wider text-faint font-semibold mb-1">Interval</div>
            <Select value={sched.interval_minutes} onChange={e => void save({ interval_minutes: Number(e.target.value) })} disabled={saving} className="h-8 text-[13px]">
              {[...new Set([...INTERVALS, sched.interval_minutes])].sort((a, b) => a - b).map(m => <option key={m} value={m}>every {intervalLabel(m)}</option>)}
            </Select>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wider text-faint font-semibold mb-1">Enabled</div>
            <Toggle checked={sched.enabled} onChange={v => void save({ enabled: v })} disabled={saving} label={sched.enabled ? 'On' : 'Off'} />
          </div>
          <Stat label="Last run" value={sched.last_run ? relativeTime(sched.last_run) : 'never'} title={formatDateTime(sched.last_run)} />
          <div>
            <div className="text-[11px] uppercase tracking-wider text-faint font-semibold mb-1">Last status</div>
            <Badge tone={statusTone}>{sched.last_status ?? 'none'}</Badge>
          </div>
          <Stat label="Next run" value={sched.enabled && sched.next_run ? relativeTime(sched.next_run) : 'paused'} title={formatDateTime(sched.next_run)} />
        </> : schedErr ? <p className="text-sm text-critical sm:col-span-5">Schedule unavailable: {schedErr}</p> : [0, 1, 2, 3, 4].map(i => <Skeleton key={i} className="h-8" />)}
      </div>
    </Card>
  )
}

function Stat({ label, value, title }: { label: string; value: string; title?: string }) {
  return <div title={title}><div className="text-[11px] uppercase tracking-wider text-faint font-semibold mb-1">{label}</div><div className="text-sm tnum">{value}</div></div>
}

const empty: ConnectionCreate = { name: '', host: '', username: 'administrator@vsphere.local', password: '', verify_tls: false, interval_minutes: 5, enabled: true }

function AddDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => Promise<void> }) {
  const [form, setForm] = useState<ConnectionCreate>(empty)
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState(false)
  const [test, setTest] = useState<ConnectionTestResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const set = <K extends keyof ConnectionCreate>(k: K, v: ConnectionCreate[K]) => setForm(f => ({ ...f, [k]: v }))
  useEffect(() => { if (open) { setForm(empty); setTest(null); setErr(null) } }, [open])
  const valid = form.name.trim() && form.host.trim() && form.username.trim() && form.password

  const runTest = async () => {
    setTesting(true); setTest(null)
    try { setTest(await testConnectionDraft(form)) }
    catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 405)) setTest({ ok: false, message: 'Pre-save test is not available on this backend. Save the connection, then use Test on its card.' })
      else setTest({ ok: false, message: e instanceof Error ? e.message : String(e) })
    } finally { setTesting(false) }
  }
  const submit = async () => {
    if (!valid) return
    setBusy(true); setErr(null)
    try { await createConnection({ ...form, name: form.name.trim(), host: form.host.trim(), username: form.username.trim() }); await onCreated() }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  return (
    <Dialog open={open} onClose={onClose} title="Add vCenter" width="max-w-xl"
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button onClick={() => void runTest()} loading={testing} disabled={!form.host || !form.username || !form.password}>Test Connection</Button>
        <Button variant="primary" onClick={() => void submit()} loading={busy} disabled={!valid}>Add</Button>
      </>}>
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={e => { e.preventDefault(); void submit() }}>
        <div className="sm:col-span-2"><Field label="Name" hint="Shown in the connection selector"><Input autoFocus value={form.name} onChange={e => set('name', e.target.value)} placeholder="Workload domain 01" /></Field></div>
        <div className="sm:col-span-2"><Field label="vCenter host"><Input value={form.host} onChange={e => set('host', e.target.value)} placeholder="vc01.example.internal" autoComplete="off" /></Field></div>
        <Field label="Username"><Input value={form.username} onChange={e => set('username', e.target.value)} autoComplete="off" /></Field>
        <Field label="Password"><Input type="password" value={form.password} onChange={e => set('password', e.target.value)} autoComplete="new-password" /></Field>
        <Field label="Scan interval">
          <Select value={form.interval_minutes} onChange={e => set('interval_minutes', Number(e.target.value))} className="w-full">
            {INTERVALS.map(m => <option key={m} value={m}>every {intervalLabel(m)}</option>)}
          </Select>
        </Field>
        <div className="flex flex-col justify-end gap-3 pb-1">
          <Toggle checked={form.verify_tls} onChange={v => set('verify_tls', v)} label="Verify TLS certificate" />
          <Toggle checked={form.enabled} onChange={v => set('enabled', v)} label="Scheduled scans enabled" />
        </div>
        <div className="sm:col-span-2 rounded-lg border border-border bg-surface-2 px-3.5 py-2.5 text-sm text-muted flex items-center gap-3 opacity-80">
          <FlaskConical size={15} className="shrink-0" /><span className="flex-1">SDDC Manager discovery</span><Badge tone="neutral">Experimental</Badge><Toggle checked={false} onChange={() => undefined} disabled />
        </div>
        {test ? <p className={`sm:col-span-2 text-sm inline-flex items-center gap-1.5 ${test.ok ? 'text-ok' : 'text-critical'}`}>{test.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}{test.message}{test.version ? <span className="text-muted">vCenter {test.version}</span> : null}</p> : null}
        {err ? <p className="sm:col-span-2 text-sm text-critical">{err}</p> : null}
        <button type="submit" className="hidden" />
      </form>
    </Dialog>
  )
}
