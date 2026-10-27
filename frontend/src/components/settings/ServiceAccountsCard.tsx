import { useState, type FormEvent } from 'react'
import { Copy, KeyRound, Plus } from 'lucide-react'
import type { ApiToken, CreatedApiToken, ServiceAccount } from '@/types'
import {
  createServiceAccount, createServiceAccountToken, listServiceAccountTokens, listServiceAccounts,
  revokeServiceAccountToken, setServiceAccountDisabled,
} from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { formatDateTime, relativeTime } from '@/lib/format'
import { Badge, Button, Card, CardHeader, ErrorState, Field, Input, Skeleton } from '@/components/ui'

function standing(t: ApiToken, now: number): { tone: 'ok' | 'neutral' | 'critical'; label: string } {
  if (t.revoked_at) return { tone: 'critical', label: 'Revoked' }
  if (Date.parse(t.expires_at) <= now) return { tone: 'neutral', label: 'Expired' }
  return { tone: 'ok', label: 'Active' }
}

// One account's tokens: several can be live at once, so a new one can be issued before the old one is revoked.
function AccountTokens({ account, onChanged }: { account: ServiceAccount; onChanged: () => void }) {
  const s = useAsync(() => listServiceAccountTokens(account.id), [account.id])
  const [name, setName] = useState('')
  const [days, setDays] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [fresh, setFresh] = useState<CreatedApiToken | null>(null)
  const [copied, setCopied] = useState(false)

  const create = async (e: FormEvent) => {
    e.preventDefault()
    setErr(null); setBusy(true)
    try {
      const n = days.trim() ? Number(days) : s.data?.default_days ?? 30
      if (!Number.isInteger(n) || n < 1) throw new Error('Lifetime is a whole number of days, at least 1.')
      setFresh(await createServiceAccountToken(account.id, name.trim(), n)); setCopied(false)
      setName(''); setDays('')
      s.reload(); onChanged()
    } catch (e2) { setErr(e2 instanceof Error ? e2.message : String(e2)) } finally { setBusy(false) }
  }

  const revoke = async (t: ApiToken) => {
    if (!window.confirm(`Revoke "${t.name}"? Anything using it stops working at once.`)) return
    setErr(null)
    try { await revokeServiceAccountToken(account.id, t.id); if (fresh?.info.id === t.id) setFresh(null); s.reload(); onChanged() } catch (e2) { setErr(e2 instanceof Error ? e2.message : String(e2)) }
  }

  const copy = async () => {
    if (!fresh) return
    try { await navigator.clipboard.writeText(fresh.token); setCopied(true) } catch { /* the secret is selectable in the box */ }
  }

  const now = Date.now()
  return (
    <div className="space-y-3 px-3 py-3 bg-surface-2/40">
      {fresh ? (
        <div className="rounded-md border border-accent/40 bg-surface-2 px-3 py-3 space-y-2" role="status">
          <p className="text-sm text-fg">Copy this token now. It is shown once and cannot be read back later.</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 rounded-md bg-bg border border-border px-3 py-2 text-xs font-mono break-all select-all" data-testid="new-service-token">{fresh.token}</code>
            <Button type="button" size="sm" onClick={() => void copy()}><Copy size={14} /> {copied ? 'Copied' : 'Copy'}</Button>
          </div>
          <Button type="button" size="sm" variant="ghost" onClick={() => setFresh(null)}>I have copied it</Button>
        </div>
      ) : null}
      <form onSubmit={e => void create(e)} className="grid sm:grid-cols-[1fr_10rem_auto] gap-3 items-end" noValidate>
        <Field label="New token name" hint="For example the month, so the old one is easy to spot when you rotate.">
          <Input value={name} onChange={e => setName(e.target.value)} placeholder="rotation-2" maxLength={100} autoComplete="off" disabled={busy || !!account.disabled_at} />
        </Field>
        <Field label="Lifetime (days)" hint={s.data ? `Empty means ${s.data.default_days}.` : ''}>
          <Input value={days} onChange={e => setDays(e.target.value)} inputMode="numeric" placeholder={String(s.data?.default_days ?? 30)} autoComplete="off" disabled={busy || !!account.disabled_at} />
        </Field>
        <Button type="submit" loading={busy} disabled={!name.trim() || !!account.disabled_at}><KeyRound size={15} /> Issue token</Button>
      </form>
      {err ? <p className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2" role="alert">{err}</p> : null}
      {s.error && !s.data ? <ErrorState title="Tokens unavailable" error={s.error} onRetry={s.reload} /> : !s.data ? <Skeleton className="h-16 rounded-md" /> : s.data.tokens.length === 0 ? (
        <p className="text-sm text-muted">This account has no tokens.</p>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border bg-surface">
          {s.data.tokens.map(t => {
            const st = standing(t, now)
            return (
              <li key={t.id} className="flex items-center gap-3 px-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2"><span className="text-sm text-fg truncate">{t.name}</span><Badge tone={st.tone}>{st.label}</Badge></div>
                  <p className="text-xs text-faint">
                    <span className="font-mono">{t.hint}…</span> · created {formatDateTime(t.created_at)} · expires {formatDateTime(t.expires_at)} · {t.last_used_at ? `last used ${relativeTime(t.last_used_at)}` : 'never used'}
                  </p>
                </div>
                {st.label === 'Active' ? <Button type="button" size="sm" variant="danger" onClick={() => void revoke(t)}>Revoke</Button> : null}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

// Machine identities that belong to no person. Administrators manage them from a browser session.
export default function ServiceAccountsCard() {
  const s = useAsync(() => listServiceAccounts(), [])
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)

  const create = async (e: FormEvent) => {
    e.preventDefault()
    setErr(null); setBusy(true)
    try {
      const a = await createServiceAccount(name.trim(), description.trim(), s.data?.roles[0] ?? 'admin')
      setName(''); setDescription(''); setOpen(a.id)
      s.reload()
    } catch (e2) { setErr(e2 instanceof Error ? e2.message : String(e2)) } finally { setBusy(false) }
  }

  const toggle = async (a: ServiceAccount) => {
    const disabling = !a.disabled_at
    if (disabling && !window.confirm(`Disable "${a.name}"? Every token it has stops working at once.`)) return
    setErr(null)
    try { await setServiceAccountDisabled(a.id, disabling); s.reload() } catch (e2) { setErr(e2 instanceof Error ? e2.message : String(e2)) }
  }

  return (
    <Card>
      <CardHeader title="Service accounts" subtitle="Machine identities for scripts and CI that should not depend on a person. Each has a role and its own tokens, sent as Authorization: Bearer <token>." />
      <div className="px-5 pb-5 space-y-4">
        <form onSubmit={e => void create(e)} className="grid sm:grid-cols-[1fr_1fr_auto] gap-3 items-end" noValidate>
          <Field label="Name" hint="Must be unique.">
            <Input value={name} onChange={e => setName(e.target.value)} placeholder="ci-runner" maxLength={100} autoComplete="off" disabled={busy} />
          </Field>
          <Field label="Description" hint="Optional: what uses it.">
            <Input value={description} onChange={e => setDescription(e.target.value)} placeholder="nightly export job" maxLength={300} autoComplete="off" disabled={busy} />
          </Field>
          <Button type="submit" loading={busy} disabled={!name.trim()}><Plus size={15} /> Create account</Button>
        </form>
        {err ? <p className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2" role="alert">{err}</p> : null}

        {s.error && !s.data ? <ErrorState title="Service accounts unavailable" error={s.error} onRetry={s.reload} /> : !s.data ? <Skeleton className="h-24 rounded-md" /> : s.data.accounts.length === 0 ? (
          <p className="text-sm text-muted">There are no service accounts.</p>
        ) : (
          <ul className="divide-y divide-border rounded-md border border-border">
            {s.data.accounts.map(a => (
              <li key={a.id}>
                <div className="flex items-center gap-3 px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-fg truncate">{a.name}</span>
                      <Badge tone={a.disabled_at ? 'critical' : 'ok'}>{a.disabled_at ? 'Disabled' : 'Enabled'}</Badge>
                      <Badge tone="neutral">{a.role}</Badge>
                    </div>
                    <p className="text-xs text-faint">
                      {a.description ? `${a.description} · ` : ''}{a.active_tokens} active token{a.active_tokens === 1 ? '' : 's'} · created {formatDateTime(a.created_at)}
                    </p>
                  </div>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(open === a.id ? null : a.id)}>{open === a.id ? 'Hide tokens' : 'Tokens'}</Button>
                  <Button type="button" size="sm" variant={a.disabled_at ? 'secondary' : 'danger'} onClick={() => void toggle(a)}>{a.disabled_at ? 'Enable' : 'Disable'}</Button>
                </div>
                {open === a.id ? <AccountTokens account={a} onChanged={s.reload} /> : null}
              </li>
            ))}
          </ul>
        )}
        <ul className="text-xs text-faint space-y-1 list-disc pl-4">
          <li>An account can hold several tokens at once: issue the new one, move the job over, then revoke the old one.</li>
          <li>Disabling an account stops all its tokens at once; enabling it restores the ones not revoked or expired. A token cannot manage accounts or tokens; do that here.</li>
        </ul>
      </div>
    </Card>
  )
}
