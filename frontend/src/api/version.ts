import { apiGet } from './client'
import { USE_MOCKS, delay } from './mocks'

export interface VersionResponse {
  version: string
  sha: string
  built_at: string
  python: string
}

export function getVersion(): Promise<VersionResponse> {
  if (USE_MOCKS) return delay({ version: 'dev', sha: 'unknown', built_at: 'unknown', python: 'unknown' }, 80)
  return apiGet<VersionResponse>('/version')
}
