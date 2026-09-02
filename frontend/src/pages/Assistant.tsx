import { useMemo } from 'react'
import { getChanges, getEvents, getFindings, getResources, getSnapshots } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { useAppState } from '@/state/AppState'
import { useAssistantDrawer } from '@/state/AssistantState'
import { AssistantPanel } from '@/components/assistant/AssistantPanel'
import { Card } from '@/components/ui'
import type { Change, Event } from '@/types'

// Full page assistant. Context = current findings, latest changes, a bounded set of resources and the last day of
// notable vCenter events (user actions, warnings, errors) for the selected scope.
export default function AssistantPage() {
  const { connectionId, refreshKey } = useAppState()
  const { seed, seedKey } = useAssistantDrawer()
  const findings = useAsync(() => getFindings(connectionId), [connectionId, refreshKey])
  const resources = useAsync(() => getResources(connectionId), [connectionId, refreshKey])
  const changes = useAsync(async () => {
    const snaps = (await getSnapshots(connectionId)).slice().sort((a, b) => b.created_at.localeCompare(a.created_at))
    const byConn = new Map<string, typeof snaps>()
    for (const s of snaps) { const l = byConn.get(s.connection_id) ?? []; l.push(s); byConn.set(s.connection_id, l) }
    const out: Change[] = []
    for (const [cid, list] of byConn) if (list.length >= 2) out.push(...await getChanges(cid, list[1].id, list[0].id))
    return out
  }, [connectionId, refreshKey])
  // Events are best effort: the page still works if the endpoint is missing or slow.
  const events = useAsync<Event[]>(() => getEvents({ connectionId, limit: 100 }).catch(() => []), [connectionId, refreshKey])

  const context = useMemo(() => {
    const f = findings.data ?? []
    const c = changes.data ?? []
    const all = resources.data ?? []
    const interesting = new Set<string>([...f.map(x => x.resource_id ?? ''), ...c.map(x => x.resource_id)])
    const r = all.filter(x => interesting.has(x.id) || ['vcenter', 'datacenter', 'cluster', 'host'].includes(x.type)).slice(0, 40)
    const e = (events.data ?? []).filter(x => x.category !== 'info').slice(0, 40)
    return { findings: f, changes: c, resources: r, events: e }
  }, [findings.data, changes.data, resources.data, events.data])

  return (
    <div className="anim-fade-up h-[calc(100vh-8rem)] min-h-[560px] flex flex-col">
      <div className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight">Assistant</h1>
        <p className="text-sm text-muted mt-1">Evidence-grounded explanations, investigation plans and reviewable scripts. Read-only: nothing here executes against your environment.</p>
      </div>
      <Card className="flex-1 min-h-0 overflow-hidden">
        <AssistantPanel seed={seed} seedKey={seedKey} context={context} />
      </Card>
    </div>
  )
}
