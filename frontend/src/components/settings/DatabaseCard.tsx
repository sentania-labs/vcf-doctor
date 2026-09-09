import { useEffect, useState } from 'react'
import { Database } from 'lucide-react'
import { getReadiness } from '@/api'
import { Badge, Card, CardHeader } from '@/components/ui'

// Where the database is and what it is called is a deployment binding, not a
// setting, so it is not shown or edited here. What the console owes an operator
// is whether it can reach the database at all, which is also the one question
// it can still answer when it cannot.
type Reachability = 'checking' | 'healthy' | 'unavailable'

export default function DatabaseCard() {
  const [state, setState] = useState<Reachability>('checking')
  useEffect(() => {
    let cancelled = false
    // Readiness is 503 while the database is unreachable, so the rejection and
    // the database:false body are the same answer: not usable.
    getReadiness()
      .then(r => { if (!cancelled) setState(r.database === false ? 'unavailable' : 'healthy') })
      .catch(() => { if (!cancelled) setState('unavailable') })
    return () => { cancelled = true }
  }, [])

  const badge = state === 'checking'
    ? <Badge tone="neutral">Checking</Badge>
    : state === 'healthy'
      ? <Badge tone="ok" dot>Healthy</Badge>
      : <Badge tone="critical" dot>Unavailable</Badge>

  return (
    <Card>
      <CardHeader title="Database" subtitle="PostgreSQL holds every connection, snapshot, finding, change and event." action={badge} />
      <div className="px-5 pb-5">
        <div className="flex items-start gap-2 text-xs text-faint bg-surface-2 rounded-md px-3 py-2">
          <Database size={14} className="mt-0.5 shrink-0" />
          <span>{state === 'unavailable'
            ? 'The console cannot reach its database, so nothing is being recorded and history cannot be read. The connection is set by whoever deployed this instance; fix it there.'
            : 'The connection is set by whoever deployed this instance and is not editable here.'}</span>
        </div>
      </div>
    </Card>
  )
}
