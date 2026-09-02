import type { Change, ChangeLogEntry, Significance } from '@/types'
import { apiGet } from './client'
import { qs } from '@/lib/format'
import { USE_MOCKS, delay, mockEstate, mockState } from './mocks'

export const SIGNIFICANCE_RANK: Record<Significance, number> = { low: 0, medium: 1, high: 2 }

// minSignificance omitted: the backend applies the Settings default (changes_min_significance).
export function getChanges(connectionId: string, from: string, to: string, minSignificance?: Significance): Promise<Change[]> {
  if (USE_MOCKS) {
    if (from === to) return delay([])
    const estate = mockEstate(connectionId)[0]
    const floor = SIGNIFICANCE_RANK[minSignificance ?? mockState.settings.changes_min_significance ?? 'low']
    const changes = (estate?.changes ?? []).filter(c => SIGNIFICANCE_RANK[c.significance] >= floor)
    // Older baselines show the full set; adjacent snapshots show a subset.
    const snaps = estate?.snapshots ?? []
    const fi = snaps.findIndex(s => s.id === from)
    const ti = snaps.findIndex(s => s.id === to)
    const span = fi >= 0 && ti >= 0 ? Math.abs(fi - ti) : 3
    return delay(span >= 2 ? changes : changes.filter((_, i) => i % 2 === 0), 350)
  }
  return apiGet<Change[]>(`/changes${qs({ connection_id: connectionId, from, to, min_significance: minSignificance })}`)
}

export interface ChangeLogQuery {
  connectionId: string
  since?: string | null
  until?: string | null
  minSignificance?: Significance | null
  resourceId?: string | null
  limit?: number | null
}

// GET /changes/log: the persisted per-scan diff rows, newest first. Backend defaults: last 24 h, limit 500.
export function getChangeLog(query: ChangeLogQuery): Promise<ChangeLogEntry[]> {
  if (USE_MOCKS) {
    const estate = mockEstate(query.connectionId)[0]
    const sinceMs = query.since ? new Date(query.since).getTime() : Date.now() - 86_400_000
    const untilMs = query.until ? new Date(query.until).getTime() : Number.POSITIVE_INFINITY
    const floor = SIGNIFICANCE_RANK[query.minSignificance ?? mockState.settings.changes_min_significance ?? 'low']
    const rows = (estate?.changeLog ?? [])
      .filter(c => { const t = new Date(c.observed_at).getTime(); return t >= sinceMs && t <= untilMs })
      .filter(c => SIGNIFICANCE_RANK[c.significance] >= floor)
      .filter(c => !query.resourceId || c.resource_id === query.resourceId)
      .sort((a, b) => b.observed_at.localeCompare(a.observed_at))
    return delay(rows.slice(0, query.limit ?? 500), 300)
  }
  return apiGet<ChangeLogEntry[]>(`/changes/log${qs({
    connection_id: query.connectionId, since: query.since, until: query.until,
    min_significance: query.minSignificance, resource_id: query.resourceId, limit: query.limit,
  })}`)
}
