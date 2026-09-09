import { Database } from 'lucide-react'
import { Badge, Card, CardHeader } from '@/components/ui'
import { useAppState } from '@/state/AppState'

// Where the database is and what it is called is a deployment binding, not a
// setting, so it is not shown or edited here. What the console owes an operator
// is whether it can reach the database at all, which is also the one question
// it can still answer when it cannot.
//
// The answer is the readiness poll the app state already runs (20s, 5s while
// down), not a second request of its own: one definition of reachability, and
// the panel follows the database coming back without a page reload.
export default function DatabaseCard() {
  const { backend } = useAppState()
  const badge = backend === 'checking'
    ? <Badge tone="neutral">Checking</Badge>
    : backend === 'up'
      ? <Badge tone="ok" dot>Healthy</Badge>
      : <Badge tone="critical" dot>Unavailable</Badge>

  return (
    <Card>
      <CardHeader title="Database" subtitle="PostgreSQL holds every connection, snapshot, finding, change and event." action={badge} />
      <div className="px-5 pb-5">
        <div className="flex items-start gap-2 text-xs text-faint bg-surface-2 rounded-md px-3 py-2">
          <Database size={14} className="mt-0.5 shrink-0" />
          <span>{backend === 'down'
            ? 'This console cannot serve: its backend or its database is unreachable, so nothing is being recorded and history cannot be read. The connection is set by whoever deployed this instance; fix it there.'
            : 'The connection is set by whoever deployed this instance and is not editable here.'}</span>
        </div>
      </div>
    </Card>
  )
}
