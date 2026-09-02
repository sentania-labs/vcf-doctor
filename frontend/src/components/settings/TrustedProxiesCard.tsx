import { useEffect, useState, type FormEvent } from 'react'
import { AlertTriangle, CheckCircle2, Network } from 'lucide-react'
import { getTrustedProxies, updateTrustedProxies } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { Badge, Button, Card, CardHeader, ErrorState, Field, Skeleton } from '@/components/ui'

// One entry per line or comma separated; the backend normalises to CIDR notation.
function split(text: string): string[] {
  return text.split(/[\n,]/).map(s => s.trim()).filter(Boolean)
}

export default function TrustedProxiesCard() {
  const s = useAsync(() => getTrustedProxies(), [])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => { if (s.data) setText(s.data.stored.join('\n')) }, [s.data])
  const pinned = s.data?.source === 'env'
  const trusting = s.data ? s.data.trusted_proxies.length > 0 : false

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setErr(null); setSaved(false); setBusy(true)
    try {
      const next = await updateTrustedProxies(split(text))
      setText(next.stored.join('\n'))
      setSaved(true); setTimeout(() => setSaved(false), 2500)
      s.reload()
    } catch (e2) { setErr(e2 instanceof Error ? e2.message : String(e2)) } finally { setBusy(false) }
  }

  return (
    <Card>
      <CardHeader title="Trusted proxies" subtitle="Which addresses may tell VCF Doctor who the real client is (the ingress in front of it)."
        action={s.data ? (pinned ? <Badge tone="info">Set by environment</Badge> : trusting ? <Badge tone="ok" dot>Per-client lockout</Badge> : <Badge tone="neutral">Shared lockout</Badge>) : null} />
      <div className="px-5 pb-5 space-y-4">
        {s.error && !s.data ? <ErrorState title="Trusted proxies unavailable" error={s.error} onRetry={s.reload} /> : !s.data ? <Skeleton className="h-24 rounded-md" /> : (
          <form onSubmit={e => void submit(e)} className="space-y-4" noValidate>
            {s.data.env_problem ? <p className="text-sm text-warning bg-warning-bg rounded-md px-3 py-2" role="alert">{s.data.env_problem} Nothing is trusted until it is fixed.</p> : null}
            {s.data.ignored_forwarded_headers && s.data.peer ? (
              <div className="flex items-start gap-2.5 rounded-md bg-warning-bg px-3 py-2.5 text-sm" role="status">
                <AlertTriangle size={16} className="text-warning mt-0.5 shrink-0" />
                <div>
                  <div className="font-medium">Requests arrive from <span className="font-mono">{s.data.peer}</span>, which is not trusted, and carry forwarded headers that are being ignored.</div>
                  <div className="text-muted mt-0.5">If that is your ingress, add it (or its network) below. Until then every visitor shares one lockout and the console treats the connection as plain {s.data.scheme}: no HSTS, no Secure cookie flag.</div>
                </div>
              </div>
            ) : null}
            {pinned ? (
              <p className="text-sm text-muted bg-surface-2 rounded-md px-3 py-2">
                VCF_DOCTOR_TRUSTED_PROXIES is set on the deployment and wins over this page. In effect: <span className="font-mono">{s.data.trusted_proxies.join(', ')}</span>. Unset the variable to manage the list here.
              </p>
            ) : null}
            <Field label="Trusted proxy addresses" hint="One IP or CIDR range per line, for example 10.42.0.0/16 for the ingress controller pods. Empty trusts nobody.">
              <textarea
                value={text} onChange={e => setText(e.target.value)} disabled={busy || pinned} rows={3} spellCheck={false}
                placeholder={'10.42.0.0/16'}
                className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm font-mono text-fg placeholder:text-faint transition-colors focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/25 disabled:opacity-50"
              />
            </Field>
            <ul className="text-xs text-faint space-y-1 list-disc pl-4">
              <li>Failed sign-ins are counted per client address. With nothing trusted, everyone behind the ingress shares one address and one lockout: five wrong passwords from anyone pause sign-in for all.</li>
              <li>Trust the ingress and each client gets its own lockout; one guesser cannot lock out the rest. HTTPS detection (the HSTS header, secure cookies) also relies on a trusted proxy reporting the scheme.</li>
              <li>Only trust addresses you control. Anything listed here can claim any client address.</li>
            </ul>
            {err ? <p className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2" role="alert">{err}</p> : null}
            <div className="flex items-center gap-3">
              <Button type="submit" loading={busy} disabled={pinned}><Network size={15} /> Save trusted proxies</Button>
              {saved ? <span className="text-sm text-ok inline-flex items-center gap-1.5"><CheckCircle2 size={15} /> Saved</span> : null}
            </div>
          </form>
        )}
      </div>
    </Card>
  )
}
