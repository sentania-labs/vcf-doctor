import { apiGet } from './client'
import { USE_MOCKS, delay } from './mocks'

// `database` is false while PostgreSQL is unreachable or a schema migration is
// still pending. It is the one thing this endpoint can still answer then, which
// is why the Settings database panel reads it here and not from /settings.
export interface HealthResponse { status: string; version: string; scheduler?: boolean; database?: boolean; [k: string]: unknown }

export function getHealth(): Promise<HealthResponse> {
  if (USE_MOCKS) return delay({ status: 'ok', version: 'dev', mode: 'mock', scheduler: true, database: true }, 80)
  return apiGet<HealthResponse>('/health')
}
