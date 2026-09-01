import type { Finding, Severity } from '@/types'
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
