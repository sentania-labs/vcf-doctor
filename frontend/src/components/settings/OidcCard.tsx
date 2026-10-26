import { useEffect, useState, type FormEvent } from 'react'
import { CheckCircle2, KeyRound } from 'lucide-react'
import type { OidcValues } from '@/types'
import { getOidcSettings, updateOidcSettings } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { Badge, Button, Card, CardHeader, ErrorState, Field, Input, Skeleton } from '@/components/ui'

const ENV_NAME: Record<keyof OidcValues, string> = {
  issuer: 'VCF_DOCTOR_OIDC_ISSUER', client_id: 'VCF_DOCTOR_OIDC_CLIENT_ID', redirect_uri: 'VCF_DOCTOR_OIDC_REDIRECT_URI',
  scopes: 'VCF_DOCTOR_OIDC_SCOPES', groups_claim: 'VCF_DOCTOR_OIDC_GROUPS_CLAIM', admin_groups: 'VCF_DOCTOR_OIDC_ADMIN_GROUPS',
}

// One group per line or comma separated; a group name may contain spaces.
function splitGroups(text: string): string[] {
  return text.split(/[\n,]/).map(s => s.trim()).filter(Boolean)
}

const textareaCls = 'w-full rounded-md border border-border bg-bg px-3 py-2 text-sm font-mono text-fg placeholder:text-faint transition-colors focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/25 disabled:opacity-50'

export default function OidcCard() {
  const s = useAsync(() => getOidcSettings(), [])
  const [form, setForm] = useState<Omit<OidcValues, 'admin_groups'>>({ issuer: '', client_id: '', redirect_uri: '', scopes: '', groups_claim: '' })
  const [groups, setGroups] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!s.data) return
    const { admin_groups, ...rest } = s.data.stored
    setForm(rest); setGroups(admin_groups.join('\n'))
  }, [s.data])

  const pinned = s.data?.pinned
  const shown = (k: keyof OidcValues): string => (pinned?.[k] && s.data ? [s.data.effective[k]].flat().join(', ') : '')
  const set = (k: keyof typeof form) => (v: string) => setForm(f => ({ ...f, [k]: v }))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setErr(null); setSaved(false); setBusy(true)
    try {
      await updateOidcSettings({ ...form, admin_groups: splitGroups(groups) })
      setSaved(true); setTimeout(() => setSaved(false), 2500)
      s.reload()
    } catch (e2) { setErr(e2 instanceof Error ? e2.message : String(e2)) } finally { setBusy(false) }
  }

  const field = (k: keyof typeof form, label: string, hint: string, placeholder = '') => (
    <Field label={label} hint={pinned?.[k] ? `Set by ${ENV_NAME[k]} on the deployment: ${shown(k)}` : hint}>
      <Input value={pinned?.[k] ? shown(k) : form[k]} onChange={e => set(k)(e.target.value)} disabled={busy || !!pinned?.[k]}
        placeholder={placeholder} spellCheck={false} autoComplete="off" className="font-mono" />
    </Field>
  )

  return (
    <Card>
      <CardHeader title="Single sign-on" subtitle="Let people sign in through your OpenID Connect provider. The operator password stays as break-glass."
        action={s.data ? (s.data.configured ? <Badge tone="ok" dot>On</Badge> : <Badge tone="neutral">Off</Badge>) : null} />
      <div className="px-5 pb-5 space-y-4">
        {s.error && !s.data ? <ErrorState title="Single sign-on settings unavailable" error={s.error} onRetry={s.reload} /> : !s.data ? <Skeleton className="h-40 rounded-md" /> : (
          <form onSubmit={e => void submit(e)} className="space-y-4" noValidate>
            {!s.data.auth_enabled ? (
              <p className="text-sm text-warning bg-warning-bg rounded-md px-3 py-2" role="status">Authentication is switched off by the deployment (VCF_DOCTOR_AUTH), so nothing here is used.</p>
            ) : null}
            <div className="grid sm:grid-cols-2 gap-4">
              {field('issuer', 'Issuer URL', 'The provider\'s issuer, for example https://auth.example.com/application/o/vcf-doctor/. Its discovery document is read from there.', 'https://auth.example.com/application/o/vcf-doctor/')}
              {field('client_id', 'Client ID', 'Registered at the provider for this console.')}
              {field('redirect_uri', 'Redirect URI', 'Optional. Leave empty to use the address below; set it when the console sits behind an ingress that changes the scheme or host.', s.data.callback_url)}
              {field('scopes', 'Scopes', `Space separated; openid is always requested. Empty means ${s.data.defaults.scopes}. Many providers need a scope such as groups for the groups claim to be sent.`, s.data.defaults.scopes)}
              {field('groups_claim', 'Groups claim', `The claim that lists a person's groups. Empty means ${s.data.defaults.groups_claim}.`, s.data.defaults.groups_claim)}
              <Field label="Admin groups" hint={pinned?.admin_groups ? `Set by ${ENV_NAME.admin_groups} on the deployment: ${shown('admin_groups')}` : 'Members of any of these get full access, the same as the operator password. One per line, matched exactly. Anyone else who signs in is refused. No group is assumed: with none listed, single sign-on stays off.'}>
                <textarea value={pinned?.admin_groups ? shown('admin_groups').split(', ').join('\n') : groups} onChange={e => setGroups(e.target.value)}
                  disabled={busy || !!pinned?.admin_groups} rows={3} spellCheck={false} className={textareaCls} placeholder={'one group per line'} />
              </Field>
            </div>
            <ul className="text-xs text-faint space-y-1 list-disc pl-4">
              <li>Register this redirect URI at the provider: <span className="font-mono text-muted break-all">{s.data.effective.redirect_uri || s.data.callback_url}</span></li>
              <li>Client secret: {s.data.client_secret ? 'a secret file is mounted and will be used.' : 'no secret file found, so this is treated as a public client (PKCE only). To use a secret, mount it as a file and point VCF_DOCTOR_OIDC_CLIENT_SECRET_FILE at it; it is never entered or stored here.'}</li>
              <li>A person's groups are checked at every sign-in. Removing someone from a group stops new sign-ins; a session they already have lasts until it expires or the operator password is changed.</li>
            </ul>
            {!s.data.configured && s.data.problems.length > 0 && (form.issuer || form.client_id || groups) ? (
              <p className="text-sm text-muted bg-surface-2 rounded-md px-3 py-2">Not on yet: {s.data.problems.join('; ')}.</p>
            ) : null}
            {err ? <p className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2" role="alert">{err}</p> : null}
            <div className="flex items-center gap-3">
              <Button type="submit" loading={busy}><KeyRound size={15} /> Save single sign-on</Button>
              {saved ? <span className="text-sm text-ok inline-flex items-center gap-1.5"><CheckCircle2 size={15} /> Saved</span> : null}
            </div>
          </form>
        )}
      </div>
    </Card>
  )
}
