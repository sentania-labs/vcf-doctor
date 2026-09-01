import { apiGet } from './client'
import { USE_MOCKS, delay } from './mocks'

export interface HealthResponse { status: string; [k: string]: unknown }

export function getHealth(): Promise<HealthResponse> {
  if (USE_MOCKS) return delay({ status: 'ok', mode: 'mock' }, 80)
  return apiGet<HealthResponse>('/health')
}
