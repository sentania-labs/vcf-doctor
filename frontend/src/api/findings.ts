import type { Finding, FindingRelated, Severity } from '@/types'
import { apiGet } from './client'
import { qs } from '@/lib/format'
import { USE_MOCKS, delay, mockEstate } from './mocks'

export function getFindings(connectionId?: string | null, severity?: Severity | null): Promise<Finding[]> {
  if (USE_MOCKS) {
    const all = mockEstate(connectionId).flatMap(e => e.findings)
    return delay(severity ? all.filter(f => f.severity === severity) : all)
  }
  return apiGet<Finding[]>(`/findings${qs({ connection_id: connectionId, severity })}`)
}

// GET /findings/{id}/related: change-log rows about the finding's object and neighbours since it was first observed.
export function getFindingRelated(findingId: string, connectionId?: string | null): Promise<FindingRelated> {
  if (USE_MOCKS) {
    const estate = mockEstate(connectionId).find(e => e.findings.some(f => f.id === findingId)) ?? mockEstate(connectionId)[0]
    const finding = estate?.findings.find(f => f.id === findingId)
    const snaps = [...(estate?.snapshots ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at))
    const since = snaps[Math.min(2, Math.max(0, snaps.length - 1))]?.created_at ?? new Date(Date.now() - 86_400_000).toISOString()
    const near = new Set<string>(finding?.resource_id ? [finding.resource_id] : [])
    const changes = (estate?.changes ?? []).filter(c => near.has(c.resource_id) || c.significance === 'high').slice(0, 12)
    return delay({
      finding_id: findingId, connection_id: estate?.connection.id ?? '', resource_ids: [...near],
      window: { basis: 'first_observed', since, until: null, first_observed: since, scans_present: Math.min(3, snaps.length), capped: false },
      changes,
    }, 300)
  }
  return apiGet<FindingRelated>(`/findings/${encodeURIComponent(findingId)}/related${qs({ connection_id: connectionId })}`)
}
