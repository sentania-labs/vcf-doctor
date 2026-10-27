import { useState, type FormEvent } from 'react'
import { Copy, KeyRound } from 'lucide-react'
import type { ApiToken, CreatedApiToken } from '@/types'
import { createApiToken, listApiTokens, revokeApiToken } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { formatDateTime, relativeTime } from '@/lib/format'
import { Badge, Button, Card, CardHeader, ErrorState, Field, Input, Skeleton } from '@/components/ui'

// What became of a token, in words for the person who owns it.
function standing(t: ApiToken, now: number): { tone: 'ok' | 'neutral' | 'critical'; label: string } {
  if (t.revoked_at) return { tone: 'critical', label: t.revoked_reason && t.revoked_reason !== 'revoked' ? 'Revoked: ' + t.revoked_reason : 'Revoked' }
  if (Date.parse(t.expires_at) <= now) return { tone: 'neutral', label: 'Expired' }
  return { tone: 'ok', label: 'Active' }
}

// Only people who signed in through single sign-on have tokens; the page leaves this card out for everyone else.
export default function TokensCard() {
  const s = useAsync(() => listApiTokens(), [])
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
      if (!Number.isInteger(n) || n < 1 || n > (s.data?.max_days ?? 365)) throw new Error(`Lifetime is a whole number of days, 1 to ${s.data?.max_days ?? 365}.`)
      setFresh(await createApiToken(name.trim(), n)); setCopied(false)
      setName(''); setDays('')
      s.reload()
    } catch (e2) { setErr(e2 instanceof Error ? e2.message : String(e2)) } finally { setBusy(false) }
  }

  const revoke = async (t: ApiToken) => {
    if (!window.confirm(`Revoke "${t.name}"? Anything using it stops working at once.`)) return
    setErr(null)
    try { await revokeApiToken(t.id); if (fresh?.info.id === t.id) setFresh(null); s.reload() } catch (e2) { setErr(e2 instanceof Error ? e2.message : String(e2)) }
  }

  const copy = async () => {
    if (!fresh) return
    try { await navigator.clipboard.writeText(fresh.token); setCopied(true) } catch { /* the secret is selectable in the box */ }
  }

  const now = Date.now()
  return (
    <Card>
      <CardHeader title="Personal API tokens" subtitle="Let a script or CI job call the API as you, with your role. Send the token as Authorization: Bearer <token>." />
      <div className="px-5 pb-5 space-y-4">
        {fresh ? (
          <div className="rounded-md border border-accent/40 bg-surface-2 px-3 py-3 space-y-2" role="status">
            <p className="text-sm text-fg">Copy this token now. It is shown once and cannot be read back later.</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 rounded-md bg-bg border border-border px-3 py-2 text-xs font-mono break-all select-all" data-testid="new-token">{fresh.token}</code>
              <Button type="button" size="sm" onClick={() => void copy()}><Copy size={14} /> {copied ? 'Copied' : 'Copy'}</Button>
            </div>
            <Button type="button" size="sm" variant="ghost" onClick={() => setFresh(null)}>I have copied it</Button>
          </div>
        ) : null}

        <form onSubmit={e => void create(e)} className="grid sm:grid-cols-[1fr_10rem_auto] gap-3 items-end" noValidate>
          <Field label="Name" hint="What it is for, so you can tell tokens apart.">
            <Input value={name} onChange={e => setName(e.target.value)} placeholder="nightly export" maxLength={100} autoComplete="off" disabled={busy} />
          </Field>
          <Field label="Lifetime (days)" hint={s.data ? `Empty means ${s.data.default_days}. At most ${s.data.max_days}.` : ''}>
            <Input value={days} onChange={e => setDays(e.target.value)} inputMode="numeric" placeholder={String(s.data?.default_days ?? 30)} autoComplete="off" disabled={busy} />
          </Field>
          <Button type="submit" loading={busy} disabled={!name.trim()}><KeyRound size={15} /> Create token</Button>
        </form>
        {err ? <p className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2" role="alert">{err}</p> : null}

        {s.error && !s.data ? <ErrorState title="Tokens unavailable" error={s.error} onRetry={s.reload} /> : !s.data ? <Skeleton className="h-24 rounded-md" /> : s.data.tokens.length === 0 ? (
          <p className="text-sm text-muted">You have no tokens.</p>
        ) : (
          <ul className="divide-y divide-border rounded-md border border-border">
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
        <ul className="text-xs text-faint space-y-1 list-disc pl-4">
          <li>A token has the role you have now and is stored only as a hash. It stops when it expires, when you revoke it, or if you are refused at a later sign-in.</li>
          <li>A token cannot create or revoke tokens; do that here.</li>
        </ul>
      </div>
    </Card>
  )
}
