import type { Resource } from '@/types'
import { apiGet } from './client'
import { qs } from '@/lib/format'
import { USE_MOCKS, delay, mockEstate } from './mocks'

export function getResources(connectionId?: string | null, snapshotId?: string | null): Promise<Resource[]> {
  if (USE_MOCKS) return delay(mockEstate(connectionId).flatMap(e => e.resources))
  return apiGet<Resource[]>(`/resources${qs({ connection_id: connectionId, snapshot_id: snapshotId })}`)
}
