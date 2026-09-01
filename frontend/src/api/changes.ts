import type { Change, Significance } from '@/types'
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
