import type { Overview } from '@/types'
import { apiGet } from './client'
import { qs } from '@/lib/format'
import { USE_MOCKS, delay, mockEstate } from './mocks'

export function getOverview(connectionId?: string | null): Promise<Overview> {
  if (USE_MOCKS) {
    const estates = mockEstate(connectionId)
    const resources = estates.flatMap(e => e.resources)
    const findings = estates.flatMap(e => e.findings)
    const changes = estates.flatMap(e => e.changes)
    const hosts = resources.filter(r => r.type === 'host')
    const vms = resources.filter(r => r.type === 'vm')
    const ds = resources.filter(r => r.type === 'datastore')
    const cap = ds.reduce((a, d) => a + Number(d.properties.capacityGB ?? 0), 0)
    const free = ds.reduce((a, d) => a + Number(d.properties.freeGB ?? 0), 0)
    const counts = {
      critical: findings.filter(f => f.severity === 'critical').length,
      warning: findings.filter(f => f.severity === 'warning').length,
      info: findings.filter(f => f.severity === 'info').length,
      passed: 12 * estates.length - findings.length,
    }
    const by_type: Record<string, number> = {}
    for (const r of resources) by_type[r.type] = (by_type[r.type] ?? 0) + 1
    const score = Math.max(0, 100 - counts.critical * 25 - counts.warning * 6 - counts.info * 1)
    const last = estates.map(e => e.schedule.last_run).filter(Boolean).sort().at(-1) ?? null
    const order = { critical: 0, warning: 1, info: 2 }
    return delay({
      health_score: score, counts, resources: { total: resources.length, by_type },
      hosts_connected: hosts.filter(h => h.properties.connectionState === 'connected').length, hosts_total: hosts.length,
      vms_on: vms.filter(v => v.properties.powerState === 'poweredOn').length, vms_total: vms.length,
      storage_free_pct: cap ? Math.round((free / cap) * 100) : null, last_scan: last,
      top_findings: [...findings].sort((a, b) => order[a.severity] - order[b.severity]).slice(0, 5),
      recent_changes: changes.slice(0, 6),
    })
  }
  return apiGet<Overview>(`/overview${qs({ connection_id: connectionId })}`)
}
