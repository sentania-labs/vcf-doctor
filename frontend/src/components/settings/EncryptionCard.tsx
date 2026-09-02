import { Link } from 'react-router-dom'
import { AlertTriangle, Lock } from 'lucide-react'
import { getEncryptionStatus } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { useAppState } from '@/state/AppState'
import { Badge, Card, CardHeader, Skeleton } from '@/components/ui'

// Settings > Encryption: what protects stored secrets and whether anything needs re-entering.
export default function EncryptionCard({ reloadKey }: { reloadKey?: unknown }) {
  const st = useAsync(() => getEncryptionStatus(), [reloadKey])
  const { connections } = useAppState()
  const d = st.data
  const items = (d?.unreadable_connections ?? []).map(id => ({ id, name: connections.find(c => c.id === id)?.name ?? id }))
  const problems = items.length + (d?.assistant_key_unreadable ? 1 : 0)

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
            {problems ? (
              <div className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2 space-y-1" role="alert">
                <p>The stored secrets below were encrypted with a different key (the key was lost or rotated). Nothing else is affected; re-enter them and they are stored under the current key.</p>
                <ul className="list-disc pl-4">
                  {items.map(i => <li key={i.id}>vCenter password for <span className="font-semibold">{i.name}</span> (<Link to="/connections" className="underline">Connections</Link>)</li>)}
                  {d.assistant_key_unreadable ? <li>Anthropic API key (Assistant section above){d.assistant_env_fallback ? ', the ANTHROPIC_API_KEY environment variable is in use meanwhile' : ''}</li> : null}
                </ul>
              </div>
            ) : null}
          </>
        )}
      </div>
    </Card>
  )
}
