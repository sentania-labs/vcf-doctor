import { useEffect, useState, type FormEvent } from 'react'
import { CheckCircle2, KeyRound, ShieldCheck } from 'lucide-react'
import type { AssistantSettings, Settings, Significance } from '@/types'
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

export default function SettingsPage() {
  const s = useAsync(() => getSettings(), [])
  const status = useAsync(() => getAssistantStatus(), [s.data])
  const [retention, setRetention] = useState(96)
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

  useEffect(() => { if (s.data) { setRetention(s.data.retention); setAssistant(s.data.assistant); setMinSig(s.data.changes_min_significance ?? 'low') } }, [s.data])
  const apply = (d: Settings) => { setRetention(d.retention); setAssistant(d.assistant); setMinSig(d.changes_min_significance ?? 'low'); setApiKey('') }

  const save = async () => {
    setSaving(true); setErr(null); setSaved(false)
    try {
      const body = { retention, changes_min_significance: minSig, assistant: { enabled: assistant.enabled, provider: assistant.provider, model: assistant.model.trim() || 'claude-opus-5', ...(apiKey ? { api_key: apiKey } : {}) } }
      apply(await updateSettings(body))
      setSaved(true); setTimeout(() => setSaved(false), 2500)
      status.reload()
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) } finally { setSaving(false) }
  }

  if (s.error && !s.data) return <div className="anim-fade-up"><PageHeader title="Settings" /><Card><ErrorState title="Settings unavailable" error={s.error} onRetry={s.reload} /></Card></div>

  return (
    <div className="anim-fade-up max-w-3xl">
      <PageHeader title="Settings" subtitle="Working defaults are in place. Nothing here is required to start."
        actions={<>{saved ? <span className="text-sm text-ok inline-flex items-center gap-1.5"><CheckCircle2 size={15} /> Saved</span> : null}<Button variant="primary" onClick={() => void save()} loading={saving} disabled={!s.data}>Save changes</Button></>} />

      {!s.data ? <div className="space-y-5"><Skeleton className="h-40 rounded-xl" /><Skeleton className="h-72 rounded-xl" /></div> : (
        <div className="space-y-5">
          <Card>
            <CardHeader title="Snapshots" subtitle="How many scheduled snapshots are kept per connection before the oldest are pruned" />
            <div className="px-5 pb-5 grid sm:grid-cols-2 gap-4">
              <Field label="Scheduled snapshots kept (per connection)" hint="A count, not days. The oldest scheduled snapshots beyond this number are pruned after each scan. Manual snapshots are never pruned.">
                <Input type="number" min={1} max={3650} value={retention} onChange={e => setRetention(Math.max(1, Number(e.target.value) || 1))} />
              </Field>
            </div>
          </Card>

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
                <Field label="Provider" hint="Mock answers from canned evidence and needs no key.">
                  <Select value={assistant.provider} onChange={e => setAssistant(a => ({ ...a, provider: e.target.value as AssistantSettings['provider'] }))} className="w-full">
                    <option value="anthropic">Anthropic</option>
                    <option value="mock">Mock</option>
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
