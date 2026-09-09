import { useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, FileKey, KeyRound, Lock } from 'lucide-react'
import { getEncryptionStatus, rekeyEncryption } from '@/api'
import type { EncryptionStatus } from '@/types'
import { useAsync } from '@/hooks/useAsync'
import { useAppState } from '@/state/AppState'
import { Badge, Button, Card, CardHeader, Skeleton } from '@/components/ui'

// Settings > Encryption: what protects stored secrets and whether anything needs re-entering.
export default function EncryptionCard({ reloadKey, onRotated }: { reloadKey?: unknown; onRotated?: () => void }) {
  const st = useAsync(() => getEncryptionStatus(), [reloadKey])
  const { connections, reloadConnections } = useAppState()
  const d = st.data
  const items = (d?.unreadable_connections ?? []).map(id => ({ id, name: connections.find(c => c.id === id)?.name ?? id }))
  const problems = items.length + (d?.assistant_key_unreadable ? 1 : 0)
  const last = d?.last_rekey ?? null

  return (
    <Card>
      <CardHeader title="Encryption at rest" subtitle="vCenter passwords and the Anthropic API key are encrypted before they are written to the database."
        action={d ? (d.key_error ? <Badge tone="critical" className="whitespace-nowrap"><AlertTriangle size={11} /> Key unavailable</Badge> : problems ? <Badge tone="critical" className="whitespace-nowrap"><AlertTriangle size={11} /> {problems} need{problems === 1 ? 's' : ''} re-entry</Badge> : <Badge tone="ok" dot>Encrypted</Badge>) : null} />
      <div className="px-5 pb-5 space-y-3">
        {!d ? (st.error ? <p className="text-sm text-critical">Encryption status unavailable: {String(st.error)}</p> : <Skeleton className="h-10" />) : (
          <>
            <div className="grid sm:grid-cols-2 gap-4">
              <div>
                <div className="text-[11px] uppercase tracking-wider text-faint font-semibold mb-1">Key source</div>
                <div className="text-sm inline-flex items-center gap-1.5"><Lock size={14} className="text-muted" />
                  {d.key_source === 'env' ? <>Environment variable <span className="font-mono">{d.key_env_var}</span></> : <>Generated key file</>}
                </div>
              </div>
              <div>
                <div className="text-[11px] uppercase tracking-wider text-faint font-semibold mb-1">{d.key_source === 'env' ? 'Delivered by' : 'Key file'}</div>
                <div className="text-sm font-mono break-all">{d.key_source === 'env' ? 'the deployment (for example a sealed Kubernetes secret)' : d.key_file}</div>
              </div>
            </div>
            {d.key_error ? <p className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2" role="alert">{d.key_error}. Until this is fixed, stored passwords cannot be read and new ones cannot be saved.</p> : null}
            {d.key_source === 'file' ? (
              <p className="text-xs text-faint">The key file lives next to the database on the persistent volume with owner-only permissions. Setting <span className="font-mono">{d.key_env_var}</span> in the deployment takes precedence over it. The key is never shown here.</p>
            ) : null}
            {last ? (
              <p className={`text-xs rounded-md px-3 py-2 ${last.error && problems ? 'text-critical bg-critical-bg' : 'text-faint bg-surface-2'}`} role={last.error && problems ? 'alert' : undefined}>
                {`${last.error ? 'Rotation attempted' : 'Last rotation'} ${new Date(last.at).toLocaleString()} with the previous key from ${last.source}. ${last.message}`}
              </p>
            ) : null}
            {problems ? (
              <div className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2 space-y-1" role="alert">
                <p>The stored secrets below were encrypted with a different key (the key was lost or rotated). Nothing else is affected; re-enter them and they are stored under the current key, or recover them without re-entry as described under Rotate the encryption key below.</p>
                <ul className="list-disc pl-4">
                  {items.map(i => <li key={i.id}>vCenter password for <span className="font-semibold">{i.name}</span> (<Link to="/connections" className="underline">Connections</Link>)</li>)}
                  {d.assistant_key_unreadable ? <li>Anthropic API key (Assistant section above){d.assistant_env_fallback ? ', the ANTHROPIC_API_KEY environment variable is in use meanwhile' : ''}</li> : null}
                </ul>
              </div>
            ) : null}
            <RotateForm status={d} onRotated={fresh => { st.set(fresh); void reloadConnections(); onRotated?.() }} />
          </>
        )}
      </div>
    </Card>
  )
}

// Rotation without re-entering credentials. The key is never entered here: a
// deployment hands the previous key to the app at startup, and the one case the
// app already holds it is the generated key file left on the volume by a move
// to an environment key, which is one click away.
function RotateForm({ status, onRotated }: { status: EncryptionStatus; onRotated: (fresh: EncryptionStatus) => void }) {
  const [busy, setBusy] = useState(false)
  const [ok, setOk] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const run = async () => {
    setOk(null); setErr(null); setBusy(true)
    try {
      const r = await rekeyEncryption()
      onRotated(r.status)
      if (r.ok) setOk(r.message); else setErr(r.message)
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2))
    } finally { setBusy(false) }
  }

  return (
    <details className="rounded-md border border-border bg-surface-2">
      <summary className="cursor-pointer select-none px-3 py-2 text-sm font-medium inline-flex items-center gap-1.5"><KeyRound size={14} className="text-muted" /> Rotate the encryption key</summary>
      <div className="px-3 pb-3 pt-1 space-y-3">
        <p className="text-xs text-faint">
          Set the new key in the deployment (<span className="font-mono">{status.key_env_var}</span>) and restart. Secrets encrypted under the old key are rewritten under the new one automatically when the deployment also sets <span className="font-mono">{status.key_previous_env_var}</span> for that one restart, then drop that variable on the next deploy. Nothing is rewritten unless the previous key opens it, and no key is ever entered, stored or shown here.
        </p>
        {status.previous_key_file ? (
          <div className="space-y-2 rounded-md bg-surface px-3 py-2.5 border border-border">
            <p className="text-xs text-muted">The generated key file <span className="font-mono break-all">{status.previous_key_file}</span> is still on the volume, so the previous key is already here. Use it to move the stored secrets onto <span className="font-mono">{status.key_env_var}</span> without re-entering anything. Delete the file afterwards, once you are happy the console still reads its credentials.</p>
            <Button onClick={() => void run()} loading={busy} disabled={busy}><FileKey size={15} /> Re-encrypt from the key file</Button>
          </div>
        ) : null}
        {err ? <p className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2" role="alert">{err}</p> : null}
        {ok ? <p className="text-sm text-ok inline-flex items-center gap-1.5"><CheckCircle2 size={15} /> {ok}</p> : null}
      </div>
    </details>
  )
}
