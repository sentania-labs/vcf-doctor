import type { Change } from '@/types'
import { apiGet } from './client'
import { qs } from '@/lib/format'
import { USE_MOCKS, delay, mockEstate } from './mocks'

export function getChanges(connectionId: string, from: string, to: string): Promise<Change[]> {
  if (USE_MOCKS) {
    if (from === to) return delay([])
    const estate = mockEstate(connectionId)[0]
    const changes = estate?.changes ?? []
    // Older baselines show the full set; adjacent snapshots show a subset.
    const snaps = estate?.snapshots ?? []
    const fi = snaps.findIndex(s => s.id === from)
    const ti = snaps.findIndex(s => s.id === to)
    const span = fi >= 0 && ti >= 0 ? Math.abs(fi - ti) : 3
    return delay(span >= 2 ? changes : changes.filter((_, i) => i % 2 === 0), 350)
  }
  return apiGet<Change[]>(`/changes${qs({ connection_id: connectionId, from, to })}`)
}
