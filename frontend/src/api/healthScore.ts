import type { HealthScoreSettings, HealthWeights } from '@/types'
import { apiGet, apiSend } from './client'
import { USE_MOCKS, delay } from './mocks'

export const DEFAULT_HEALTH_WEIGHTS: HealthWeights = { critical: 40, warning: 15, info: 0 }
export const HEALTH_FORMULA = 'Score = 100 minus, for each check, weight(severity) times the share of the objects that check evaluated which have a finding, summed and floored at 0. A check with no applicable objects (or that needs a previous snapshot) counts as not evaluated rather than passed.'

let mockWeights: HealthWeights = { ...DEFAULT_HEALTH_WEIGHTS }
const mockSettings = (): HealthScoreSettings => ({ weights: { ...mockWeights }, defaults: { ...DEFAULT_HEALTH_WEIGHTS }, formula: HEALTH_FORMULA })

export function getHealthScoreSettings(): Promise<HealthScoreSettings> {
  if (USE_MOCKS) return delay(mockSettings(), 100)
  return apiGet<HealthScoreSettings>('/settings/health-score')
}

export function updateHealthScoreWeights(weights: Partial<HealthWeights>): Promise<HealthScoreSettings> {
  if (USE_MOCKS) { mockWeights = { ...mockWeights, ...weights }; return delay(mockSettings(), 200) }
  return apiSend<HealthScoreSettings>('PUT', '/settings/health-score', { weights })
}

export function resetHealthScoreWeights(): Promise<HealthScoreSettings> {
  if (USE_MOCKS) { mockWeights = { ...DEFAULT_HEALTH_WEIGHTS }; return delay(mockSettings(), 200) }
  return apiSend<HealthScoreSettings>('POST', '/settings/health-score/reset')
}
