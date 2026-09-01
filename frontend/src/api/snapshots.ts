import type { SnapshotSummary } from '@/types'
import { apiGet, apiSend } from './client'
import { qs } from '@/lib/format'
import { USE_MOCKS, delay, mockEstate, mockState } from './mocks'

export function getSnapshots(connectionId?: string | null): Promise<SnapshotSummary[]> {
  if (USE_MOCKS) {
    const all = mockEstate(connectionId).flatMap(e => e.snapshots)
    return delay([...all].sort((a, b) => b.created_at.localeCompare(a.created_at)))
  }
  return apiGet<SnapshotSummary[]>(`/snapshots${qs({ connection_id: connectionId })}`)
}

export function createSnapshot(connectionId: string, label: string): Promise<SnapshotSummary> {
  if (USE_MOCKS) {
    const estate = mockEstate(connectionId)[0]
    if (!estate) return Promise.reject(new Error('Unknown connection'))
    const snap: SnapshotSummary = {
      id: `snap-${connectionId}-${mockState.nextId++}`, created_at: new Date().toISOString(), label: label || 'Manual',
      connection_id: connectionId, scheduled: false, resource_count: estate.resources.length, tier: 'manual',
    }
    estate.snapshots.unshift(snap)
    return delay(snap, 900)
  }
  return apiSend<SnapshotSummary>('POST', '/snapshots', { connection_id: connectionId, label })
}

export function deleteSnapshot(id: string): Promise<void> {
  if (USE_MOCKS) {
    for (const e of mockState.estates) e.snapshots = e.snapshots.filter(s => s.id !== id)
    return delay(undefined, 300)
  }
  return apiSend<void>('DELETE', `/snapshots/${encodeURIComponent(id)}`)
}
