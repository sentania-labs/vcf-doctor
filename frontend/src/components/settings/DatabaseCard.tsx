import { Database } from 'lucide-react'
import { Badge, Card, CardHeader } from '@/components/ui'
import { useAppState } from '@/state/AppState'
import { useAuth } from '@/state/AuthState'
import { databaseOutage } from '@/state/databaseOutage'
import { cn } from '@/lib/cn'
import { databaseHealthPresentation } from './databaseHealth'

// Where the database is and what it is called is a deployment binding, not a
// setting, so it is not edited here. What the console owes an operator is
// whether it can reach the database at all, and what to do when it cannot.
//
// The answer is the readiness poll the app state already runs (20s, 5s while
// down) and the auth status, not a request of its own: one definition of
// reachability, and the panel follows the database coming back without a reload.
export default function DatabaseCard() {
  const { databaseHealthy } = useAppState()
  const { status } = useAuth()
  const down = databaseOutage(status, databaseHealthy)
  const health = databaseHealthPresentation(down ? false : databaseHealthy, down ? status?.reason : undefined)
  if (!health) return null
  const badge = <Badge tone={health.tone} dot>{health.label}</Badge>

  return (
    <Card>
      <CardHeader title="Database" subtitle="PostgreSQL holds every connection, snapshot, finding, change and event." action={badge} />
      <div className="px-5 pb-5">
        {/* Error: the critical pairing, text in the full foreground colour. Healthy: muted text on the sunken surface. */}
        <div role={down ? 'alert' : undefined}
          className={cn('flex items-start gap-2 text-[13px] rounded-md px-3 py-2',
            down ? 'bg-critical-bg text-fg border-l-[3px] border-critical' : 'bg-surface-2 text-muted')}>
          <Database size={14} className={cn('mt-0.5 shrink-0', down ? 'text-critical' : 'text-muted')} />
          <span>{health.message}</span>
        </div>
      </div>
    </Card>
  )
}
