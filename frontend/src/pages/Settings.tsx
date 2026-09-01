import { useEffect, useState } from 'react'
import { CheckCircle2, KeyRound } from 'lucide-react'
import type { AssistantSettings, Settings } from '@/types'
import { getSettings, updateSettings, getAssistantStatus } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { Badge, Button, Card, CardHeader, ErrorState, Field, Input, PageHeader, Select, Skeleton, Toggle } from '@/components/ui'

export default function SettingsPage() {
  const s = useAsync(() => getSettings(), [])
  const status = useAsync(() => getAssistantStatus(), [s.data])
  const [retention, setRetention] = useState(30)
  const [assistant, setAssistant] = useState<AssistantSettings>({ enabled: true, provider: 'anthropic', model: 'claude-opus-5', api_key_set: false })
  const [apiKey, setApiKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => { if (s.data) { setRetention(s.data.retention); setAssistant(s.data.assistant) } }, [s.data])
  const apply = (d: Settings) => { setRetention(d.retention); setAssistant(d.assistant); setApiKey('') }

  const save = async () => {
    setSaving(true); setErr(null); setSaved(false)
    try {
      const body = { retention, assistant: { enabled: assistant.enabled, provider: assistant.provider, model: assistant.model.trim() || 'claude-opus-5', ...(apiKey ? { api_key: apiKey } : {}) } }
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
            <CardHeader title="Snapshots" subtitle="How long scheduled snapshots are kept before pruning" />
            <div className="px-5 pb-5 grid sm:grid-cols-2 gap-4">
              <Field label="Retention (days)" hint="Manual snapshots with a label are kept regardless.">
                <Input type="number" min={1} max={3650} value={retention} onChange={e => setRetention(Math.max(1, Number(e.target.value) || 1))} />
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
                <Field label="Model">
                  <Input value={assistant.model} onChange={e => setAssistant(a => ({ ...a, model: e.target.value }))} placeholder="claude-opus-5" disabled={assistant.provider === 'mock'} />
                </Field>
              </div>
              <Field label="Anthropic API key" hint="Write-only. The key is stored on the server and never shown again. An ANTHROPIC_API_KEY environment variable takes precedence.">
                <div className="flex items-center gap-3">
                  <Input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} placeholder={assistant.api_key_set ? 'Enter a new key to replace the stored one' : 'sk-ant-...'} autoComplete="new-password" disabled={assistant.provider === 'mock'} />
                  <Badge tone={assistant.api_key_set ? 'ok' : 'neutral'} className="shrink-0"><KeyRound size={11} /> {assistant.api_key_set ? 'Key set' : 'No key'}</Badge>
                </div>
              </Field>
            </div>
          </Card>
          {err ? <p className="text-sm text-critical">{err}</p> : null}
        </div>
      )}
    </div>
  )
}
