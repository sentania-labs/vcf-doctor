import { apiGet } from './client'
import { USE_MOCKS, delay } from './mocks'

// Readiness: can this console actually serve. 503 while the database is
// unreachable or a schema migration is pending, which is when sign-in and
// every page behind it fail. That is what the top bar and the Settings
// database panel both need to know, so it is the only health call the UI
// makes.
//
// The backend also serves /api/health/live, which stays 200 while the database
// is down. That one exists for container and orchestrator liveness probes,
// where the right response to a missing database is to leave the process
// alone, and the UI has no use for it.
export interface ReadinessResponse { status: string; version: string; scheduler?: boolean; database?: boolean }

export function getReadiness(): Promise<ReadinessResponse> {
  if (USE_MOCKS) return delay({ status: 'ok', version: 'dev', scheduler: true, database: true }, 80)
  return apiGet<ReadinessResponse>('/health/ready')
}
