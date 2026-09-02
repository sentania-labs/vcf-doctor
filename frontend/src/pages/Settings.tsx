import { useEffect, useState, type ChangeEvent, type FormEvent } from 'react'
import { AlertTriangle, CheckCircle2, KeyRound, ShieldCheck } from 'lucide-react'
import type { AssistantSettings, RetentionPolicy, Settings, Significance } from '@/types'
import { getSettings, updateSettings, getAssistantStatus, changePassword, getAssistantModels, type AssistantModel } from '@/api'
import { useAuth } from '@/state/AuthState'
import { useAsync } from '@/hooks/useAsync'
import { Badge, Button, Card, CardHeader, ErrorState, Field, Input, PageHeader, Select, Skeleton, Toggle } from '@/components/ui'

function AccessCard() {
  const { status } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setErr(null); setDone(false)
    if (next.length < 8) { setErr('New password must be at least 8 characters.'); return }
    if (next !== confirm) { setErr('New passwords do not match.'); return }
    setBusy(true)
    try {
      await changePassword(current, next)
      setCurrent(''); setNext(''); setConfirm('')
      setDone(true); setTimeout(() => setDone(false), 3000)
    } catch (e2) {
      const msg = e2 instanceof Error ? e2.message : String(e2)
      setErr(msg === 'invalid password' ? 'Current password is incorrect.' : msg)
    } finally { setBusy(false) }
  }

  return (
    <Card>
      <CardHeader title="Access" subtitle="One shared operator password protects this console."
        action={status && !status.enabled ? <Badge tone="neutral">Disabled</Badge> : status?.enabled ? <Badge tone="ok" dot>Enabled</Badge> : null} />
      <div className="px-5 pb-5">
        {status && !status.enabled ? (
          <p className="text-sm text-muted bg-surface-2 rounded-md px-3 py-2">Authentication is disabled by the deployment (VCF_DOCTOR_AUTH=off).</p>
        ) : (
          <form onSubmit={e => void submit(e)} className="space-y-4" noValidate>
            <div className="grid sm:grid-cols-3 gap-4">
              <Field label="Current password">
                <Input type="password" value={current} onChange={e => setCurrent(e.target.value)} autoComplete="current-password" name="current_password" disabled={busy} />
              </Field>
              <Field label="New password" hint="At least 8 characters.">
                <Input type="password" value={next} onChange={e => setNext(e.target.value)} autoComplete="new-password" name="new_password" disabled={busy} />
              </Field>
              <Field label="Confirm new password">
                <Input type="password" value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password" name="confirm_password" disabled={busy} />
              </Field>
            </div>
            {err ? <p className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2" role="alert">{err}</p> : null}
            <div className="flex items-center gap-3">
              <Button type="submit" loading={busy} disabled={!current || !next || !confirm}><ShieldCheck size={15} /> Change password</Button>
              {done ? <span className="text-sm text-ok inline-flex items-center gap-1.5"><CheckCircle2 size={15} /> Password changed</span> : null}
            </div>
          </form>
        )}
      </div>
    </Card>
  )
}

const DEFAULT_RETENTION: RetentionPolicy = { recent_days: 14, hourly_days: 30, daily_days: 365 }

// Inline validation for the retention tiers. Returns the field in error and a message, or null.
function retentionProblem(p: RetentionPolicy): { field: keyof RetentionPolicy; message: string } | null {
  for (const k of ['recent_days', 'hourly_days', 'daily_days'] as const) {
    if (!Number.isInteger(p[k]) || p[k] < 1) return { field: k, message: 'Each window must be a whole number of days, at least 1.' }
  }
  if (p.recent_days > p.hourly_days) return { field: 'hourly_days', message: `Hourly window (${p.hourly_days} d) must be at least the every-scan window (${p.recent_days} d).` }
  if (p.hourly_days > p.daily_days) return { field: 'daily_days', message: `Daily window (${p.daily_days} d) must be at least the hourly window (${p.hourly_days} d).` }
  return null
}

function RetentionCard({ value, onChange }: { value: RetentionPolicy; onChange: (p: RetentionPolicy) => void }) {
  const problem = retentionProblem(value)
  const set = (k: keyof RetentionPolicy) => (e: ChangeEvent<HTMLInputElement>) => onChange({ ...value, [k]: e.target.value === '' ? 0 : Math.floor(Number(e.target.value)) })
  const cls = (k: keyof RetentionPolicy) => problem?.field === k ? 'border-critical focus:border-critical focus:ring-critical/25' : undefined
  return (
    <Card>
      <CardHeader title="Retention" subtitle="How long scheduled snapshots are kept, thinning out as they age. Applied per connection after every scan."
        action={problem ? <Badge tone="critical"><AlertTriangle size={11} /> Check values</Badge> : <Badge tone="ok" dot>Valid</Badge>} />
      <div className="px-5 pb-5 space-y-4">
        <div className="grid sm:grid-cols-3 gap-4">
          <Field label="Every scan kept for (days)" hint="Younger than this, every scheduled snapshot stays.">
            <Input type="number" min={1} max={3650} inputMode="numeric" value={value.recent_days || ''} onChange={set('recent_days')} className={cls('recent_days')} aria-invalid={problem?.field === 'recent_days'} />
          </Field>
          <Field label="One per hour kept for (days)" hint="Between the two windows, the snapshot nearest each hour mark stays.">
            <Input type="number" min={1} max={3650} inputMode="numeric" value={value.hourly_days || ''} onChange={set('hourly_days')} className={cls('hourly_days')} aria-invalid={problem?.field === 'hourly_days'} />
          </Field>
          <Field label="One per day kept for (days)" hint="Older than this, scheduled snapshots are removed.">
            <Input type="number" min={1} max={3650} inputMode="numeric" value={value.daily_days || ''} onChange={set('daily_days')} className={cls('daily_days')} aria-invalid={problem?.field === 'daily_days'} />
          </Field>
        </div>
        {problem ? <p className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2" role="alert">{problem.message}</p>
          : <p className="text-sm text-muted">Every scan for {value.recent_days} {value.recent_days === 1 ? 'day' : 'days'}, then hourly to {value.hourly_days} {value.hourly_days === 1 ? 'day' : 'days'}, then daily to {value.daily_days} {value.daily_days === 1 ? 'day' : 'days'}.</p>}
        <ul className="text-xs text-faint space-y-1 list-disc pl-4">
          <li>Manual snapshots are never pruned. Scheduled snapshots follow the tiers above.</li>
          <li>vCenter events and the change log follow the daily window ({value.daily_days || '?'} {value.daily_days === 1 ? 'day' : 'days'}).</li>
        </ul>
      </div>
    </Card>
  )
}

export default function SettingsPage() {
  const s = useAsync(() => getSettings(), [])
  const status = useAsync(() => getAssistantStatus(), [s.data])
  const [retention, setRetention] = useState<RetentionPolicy>(DEFAULT_RETENTION)
  const [minSig, setMinSig] = useState<Significance>('low')
  const [assistant, setAssistant] = useState<AssistantSettings>({ enabled: true, provider: 'anthropic', model: 'claude-opus-5', api_key_set: false })
  const [models, setModels] = useState<AssistantModel[]>([])
  const [modelSource, setModelSource] = useState<'live' | 'curated' | null>(null)
  useEffect(() => {
    let cancelled = false
    getAssistantModels().then(r => { if (!cancelled) { setModels(r.models); setModelSource(r.source) } }).catch(() => { if (!cancelled) setModels([]) })
    return () => { cancelled = true }
  }, [assistant.api_key_set])
  const [apiKey, setApiKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => { if (s.data) { setRetention(s.data.retention_policy ?? DEFAULT_RETENTION); setAssistant(s.data.assistant); setMinSig(s.data.changes_min_significance ?? 'low') } }, [s.data])
  const apply = (d: Settings) => { setRetention(d.retention_policy ?? DEFAULT_RETENTION); setAssistant(d.assistant); setMinSig(d.changes_min_significance ?? 'low'); setApiKey('') }
  const retentionInvalid = retentionProblem(retention) !== null

  const save = async () => {
    setSaving(true); setErr(null); setSaved(false)
    try {
      const body = { retention_policy: retention, changes_min_significance: minSig, assistant: { enabled: assistant.enabled, provider: assistant.provider, model: assistant.model.trim() || 'claude-opus-5', ...(apiKey ? { api_key: apiKey } : {}) } }
      apply(await updateSettings(body))
      setSaved(true); setTimeout(() => setSaved(false), 2500)
      status.reload()
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) } finally { setSaving(false) }
  }

  if (s.error && !s.data) return <div className="anim-fade-up"><PageHeader title="Settings" /><Card><ErrorState title="Settings unavailable" error={s.error} onRetry={s.reload} /></Card></div>

  return (
    <div className="anim-fade-up max-w-3xl">
      <PageHeader title="Settings" subtitle="Working defaults are in place. Nothing here is required to start."
        actions={<>{saved ? <span className="text-sm text-ok inline-flex items-center gap-1.5"><CheckCircle2 size={15} /> Saved</span> : null}<Button variant="primary" onClick={() => void save()} loading={saving} disabled={!s.data || retentionInvalid} title={retentionInvalid ? 'Fix the retention windows first' : undefined}>Save changes</Button></>} />

      {!s.data ? <div className="space-y-5"><Skeleton className="h-40 rounded-xl" /><Skeleton className="h-72 rounded-xl" /></div> : (
        <div className="space-y-5">
          <RetentionCard value={retention} onChange={setRetention} />

          <Card>
            <CardHeader title="Changes" subtitle="What the Changes page and the Overview's recent changes show by default" />
            <div className="px-5 pb-5 grid sm:grid-cols-2 gap-4">
              <Field label="Minimum significance shown" hint="Low shows everything. Medium hides informational noise such as usage counters. High keeps only outage-grade changes like host disconnects and vmkernel edits. The Changes page can still override this per view.">
                <Select value={minSig} onChange={e => setMinSig(e.target.value as Significance)} className="w-full">
                  <option value="low">Low (show everything)</option>
                  <option value="medium">Medium and above</option>
                  <option value="high">High only</option>
                </Select>
              </Field>
            </div>
          </Card>

          <Card>
            <CardHeader title="Assistant" subtitle="Evidence-grounded explanations and scripts. Never executes anything."
              action={status.data ? <Badge tone={status.data.available ? 'ok' : 'warning'} dot>{status.data.available ? 'Available' : 'Unavailable'}</Badge> : null} />
            <div className="px-5 pb-5 space-y-5">
              {status.data && !status.data.available && status.data.reason ? <p className="text-sm text-warning bg-warning-bg rounded-md px-3 py-2">{status.data.reason}</p> : null}
              <Toggle checked={assistant.enabled} onChange={v => setAssistant(a => ({ ...a, enabled: v }))} label="Assistant enabled" />
              <div className="grid sm:grid-cols-2 gap-4">
                <Field label="Provider" hint={assistant.provider === 'mock' ? 'Mock answers from canned evidence (test provider). Switch to Anthropic for real answers.' : 'Answers come from the Anthropic API.'}>
                  <Select value={assistant.provider} onChange={e => setAssistant(a => ({ ...a, provider: e.target.value as AssistantSettings['provider'] }))} className="w-full">
                    <option value="anthropic">Anthropic</option>
                    {/* The mock provider is for tests; it is only listed when it is already stored so it can be switched off. */}
                    {assistant.provider === 'mock' ? <option value="mock">Mock (test)</option> : null}
                  </Select>
                </Field>
                <Field label="Model" hint={modelSource === 'live' ? 'List comes from your Anthropic account.' : 'Add an API key to list the models available to your account.'}>
                  <Select value={assistant.model} onChange={e => setAssistant(a => ({ ...a, model: e.target.value }))} disabled={assistant.provider === 'mock'}>
                    {(models.length ? models : [{ id: assistant.model, display_name: assistant.model, recommended: false }]).map(m => (
                      <option key={m.id} value={m.id}>{m.display_name}{m.recommended ? ' (recommended)' : ''}</option>
                    ))}
                    {models.length > 0 && !models.some(m => m.id === assistant.model) && <option value={assistant.model}>{assistant.model}</option>}
                  </Select>
                </Field>
              </div>
              <Field label="Anthropic API key" hint="Write-only. The key is stored on the server and never shown again. A key saved here takes precedence over the ANTHROPIC_API_KEY environment variable; clear it to fall back to the environment.">
                <div className="flex items-center gap-3">
                  <Input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} placeholder={assistant.api_key_set ? 'Enter a new key to replace the stored one' : 'sk-ant-...'} autoComplete="new-password" disabled={assistant.provider === 'mock'} />
                  <Badge tone={assistant.api_key_set ? 'ok' : 'neutral'} className="shrink-0"><KeyRound size={11} /> {assistant.api_key_set ? 'Key set' : 'No key'}</Badge>
                </div>
              </Field>
            </div>
          </Card>

          <AccessCard />
          {err ? <p className="text-sm text-critical">{err}</p> : null}
        </div>
      )}
    </div>
  )
}
