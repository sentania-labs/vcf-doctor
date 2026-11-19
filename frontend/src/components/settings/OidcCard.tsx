import DeploymentHelp from './DeploymentHelp'
import { useEffect, useState, type FormEvent } from 'react'
import { CheckCircle2, KeyRound } from 'lucide-react'
import type { OidcSettings, OidcValues } from '@/types'
import { clearOidcClientSecret, getOidcSettings, setOidcClientSecret, updateOidcSettings } from '@/api'
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

const textareaCls = 'lat-textarea w-full text-sm font-mono placeholder:text-muted disabled:opacity-50'

type SecretInfo = Pick<OidcSettings, 'client_secret' | 'client_secret_source' | 'client_secret_stored' | 'client_secret_unreadable' | 'client_secret_changed_at'>
const secretInfoOf = (o: OidcSettings): SecretInfo => ({ client_secret: o.client_secret, client_secret_source: o.client_secret_source, client_secret_stored: o.client_secret_stored, client_secret_unreadable: o.client_secret_unreadable, client_secret_changed_at: o.client_secret_changed_at })

const NO_SECRET: SecretInfo = { client_secret: false, client_secret_source: 'none', client_secret_stored: false, client_secret_unreadable: false, client_secret_changed_at: null }

// refreshKey: bumped when the encryption card re-encrypts, which may make a stored secret readable again.
export default function OidcCard({ refreshKey = 0 }: { refreshKey?: number }) {
  const s = useAsync(() => getOidcSettings(), [])
  const [form, setForm] = useState<Omit<OidcValues, 'admin_groups'>>({ issuer: '', client_id: '', redirect_uri: '', scopes: '', groups_claim: '' })
  const [groups, setGroups] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // Write-only: held here only until it is sent, never filled back from the server.
  const [secret, setSecret] = useState('')
  // Secret state is kept apart from the form so saving or removing the secret never reloads (and so never resets) unsaved fields.
  const [fresh, setFresh] = useState<SecretInfo | null>(null)
  const [secretBusy, setSecretBusy] = useState(false)
  const [secretMsg, setSecretMsg] = useState<string | null>(null)
  const [secretErr, setSecretErr] = useState<string | null>(null)

  useEffect(() => {
    if (!s.data) return
    const { admin_groups, ...rest } = s.data.stored
    setForm(rest); setGroups(admin_groups.join('\n')); setFresh(null)
  }, [s.data])

  useEffect(() => {
    if (!refreshKey) return
    getOidcSettings().then(o => setFresh(secretInfoOf(o))).catch(() => { /* the card keeps what it has */ })
  }, [refreshKey])

  const sec: SecretInfo = fresh ?? (s.data ? secretInfoOf(s.data) : NO_SECRET)

  const pinned = s.data?.pinned
  const shown = (k: keyof OidcValues): string => (pinned?.[k] && s.data ? [s.data.effective[k]].flat().join(', ') : '')
  const set = (k: keyof typeof form) => (v: string) => setForm(f => ({ ...f, [k]: v }))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setErr(null); setSaved(false); setSecretErr(null); setSecretMsg(null); setBusy(true)
    try {
      await updateOidcSettings({ ...form, admin_groups: splitGroups(groups) })
      // One save: a secret typed into the field goes up with the rest, and "Saved" means both landed.
      if (secret.trim()) {
        try { await setOidcClientSecret(secret); setSecret('') } catch (e3) {
          s.reload()
          setErr(`The other single sign-on settings were saved, but the client secret was not: ${e3 instanceof Error ? e3.message : String(e3)}`)
          return
        }
      }
      setSaved(true); setTimeout(() => setSaved(false), 2500)
      s.reload()
    } catch (e2) { setErr(e2 instanceof Error ? e2.message : String(e2)) } finally { setBusy(false) }
  }

  const saveSecret = async () => {
    setSecretErr(null); setSecretMsg(null); setSecretBusy(true)
    try {
      setFresh(secretInfoOf(await setOidcClientSecret(secret)))
      setSecret(''); setSecretMsg('Client secret saved.')
    } catch (e2) { setSecretErr(e2 instanceof Error ? e2.message : String(e2)) } finally { setSecretBusy(false) }
  }
  const removeSecret = async () => {
    const mounted = sec?.client_secret_source === 'deployment'
    if (!window.confirm(mounted
      ? 'Remove the saved client secret? The secret file mounted by the deployment keeps being used, so sign-in is not affected.'
      : 'Remove the saved client secret? Sign-in will then run as a public client, and it will fail if your provider requires a client secret.')) return
    setSecretErr(null); setSecretMsg(null); setSecretBusy(true)
    try {
      setFresh(secretInfoOf(await clearOidcClientSecret()))
      setSecret(''); setSecretMsg('Saved client secret removed.')
    } catch (e2) { setSecretErr(e2 instanceof Error ? e2.message : String(e2)) } finally { setSecretBusy(false) }
  }

  const field = (k: keyof typeof form, label: string, hint: string, placeholder = '') => (
    <Field label={label} hint={pinned?.[k] ? `Set by the deployment: ${shown(k)}` : hint}>
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
              <p className="lat-banner lat-banner--warn" role="status">Authentication is switched off by the deployment, so nothing here is used.</p>
            ) : null}
            <div className="grid sm:grid-cols-2 gap-4">
              {field('issuer', 'Issuer URL', 'The provider\'s issuer, for example https://auth.example.com/application/o/vcf-doctor/. Its discovery document is read from there.', 'https://auth.example.com/application/o/vcf-doctor/')}
              {field('client_id', 'Client ID', 'Registered at the provider for this console.')}
              {field('redirect_uri', 'Redirect URI', 'Optional. Leave empty to use the address below; set it when the console sits behind an ingress that changes the scheme or host.', s.data.callback_url)}
              {field('scopes', 'Scopes', `Space separated; openid is always requested. Empty means ${s.data.defaults.scopes}. Many providers need a scope such as groups for the groups claim to be sent.`, s.data.defaults.scopes)}
              {field('groups_claim', 'Groups claim', `The claim that lists a person's groups. Empty means ${s.data.defaults.groups_claim}.`, s.data.defaults.groups_claim)}
              <Field label="Admin groups" hint={pinned?.admin_groups ? `Set by the deployment: ${shown('admin_groups')}` : 'Members of any of these get full access, the same as the operator password. One per line, matched exactly. Anyone else who signs in is refused. No group is assumed: with none listed, single sign-on stays off.'}>
                <textarea value={pinned?.admin_groups ? shown('admin_groups').split(', ').join('\n') : groups} onChange={e => setGroups(e.target.value)}
                  disabled={busy || !!pinned?.admin_groups} rows={3} spellCheck={false} className={textareaCls} placeholder={'one group per line'} />
              </Field>
            </div>
            <div className="space-y-2" role="group" aria-label="Client secret">
              <Field label="Client secret" hint="Only for a provider that requires a confidential client. It is stored encrypted, never shown again, and used for sign-in when no secret file is mounted.">
                <div className="flex flex-wrap items-center gap-2">
                  <Input type="password" value={secret} onChange={e => setSecret(e.target.value)} disabled={secretBusy}
                    onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); if (secret.trim()) void saveSecret() } }}
                    placeholder={sec.client_secret_stored ? 'Enter a new secret to replace the saved one' : 'Enter a secret to save'}
                    autoComplete="new-password" spellCheck={false} className="min-w-0 flex-1 basis-48" />
                  <Badge tone={sec.client_secret_unreadable ? 'critical' : sec.client_secret_stored || sec.client_secret ? 'ok' : 'neutral'} className="shrink-0">
                    <KeyRound size={11} /> {sec.client_secret_unreadable ? 'Re-enter secret' : sec.client_secret_stored ? 'Secret set' : sec.client_secret ? 'Mounted file in use' : 'Not set'}
                  </Badge>
                </div>
              </Field>
              {sec.client_secret_stored && sec.client_secret_changed_at && !sec.client_secret_unreadable ? (
                <p className="text-xs text-muted">Last changed {new Date(sec.client_secret_changed_at).toLocaleString()}.</p>
              ) : null}
              {sec.client_secret_source === 'deployment' ? (
                <p className="lat-banner lat-banner--warn" role="status">A secret file mounted by the deployment is in use and takes precedence. {sec.client_secret_stored ? 'The secret saved here is kept but not used until that file is removed.' : 'Anything saved here is not used while that file is mounted.'}</p>
              ) : null}
              {sec.client_secret_unreadable ? (
                <p className="lat-banner lat-banner--bad" role="alert">The saved client secret was encrypted with a different key and cannot be read, so it is not being used. Enter it again to store it under the current encryption key.</p>
              ) : null}
              {secretErr ? <p className="lat-banner lat-banner--bad" role="alert">{secretErr}</p> : null}
              <div className="flex flex-wrap items-center gap-3">
                <Button type="button" variant="secondary" onClick={() => void saveSecret()} loading={secretBusy} disabled={!secret.trim()}>{sec.client_secret_stored ? 'Replace client secret' : 'Save client secret'}</Button>
                {sec.client_secret_stored ? <Button type="button" variant="secondary" onClick={() => void removeSecret()} disabled={secretBusy}>Remove saved secret</Button> : null}
                {secretMsg ? <span className="text-sm text-ok inline-flex items-center gap-1.5" role="status"><CheckCircle2 size={15} /> {secretMsg}</span> : null}
              </div>
            </div>
            <ul className="text-xs text-faint space-y-1 list-disc pl-4">
              <li>Register this redirect URI at the provider: <span className="font-mono text-muted break-all">{s.data.effective.redirect_uri || s.data.callback_url}</span></li>
              <li>Client secret: {sec.client_secret_source === 'deployment' ? 'a secret file mounted by the deployment is in use, so the field above is not used.' : sec.client_secret_source === 'stored' ? 'the secret saved above is used.' : 'none set, so this is treated as a public client (PKCE only). Providers that require a confidential client need a secret above.'}</li>
              <li>A person's groups are checked at every sign-in. Removing someone from a group stops new sign-ins, and their next refused sign-in ends the sessions they already have. If they never try again, a session lasts until it expires or the operator password is changed.</li>
            </ul>
            <DeploymentHelp>Authentication: VCF_DOCTOR_AUTH. Client secret file: VCF_DOCTOR_OIDC_CLIENT_SECRET_FILE. {Object.entries(ENV_NAME).filter(([key]) => pinned?.[key as keyof OidcValues]).map(([key, name]) => <span key={key}>{key}: {name}. </span>)}</DeploymentHelp>
            {!s.data.configured && s.data.problems.length > 0 && (form.issuer || form.client_id || groups) ? (
              <p className="text-sm text-muted bg-surface-2 rounded-md px-3 py-2">Not on yet: {s.data.problems.join('; ')}.</p>
            ) : null}
            {err ? <p className="lat-banner lat-banner--bad" role="alert">{err}</p> : null}
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
