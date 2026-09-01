import type { Settings, SettingsUpdate, Significance } from '@/types'
import { apiGet, apiSend } from './client'
import { USE_MOCKS, delay, mockState } from './mocks'

export function getSettings(): Promise<Settings> {
  if (USE_MOCKS) return delay(mockState.settings, 120)
  return apiGet<Settings>('/settings')
}

export function updateSettings(body: SettingsUpdate): Promise<Settings> {
  if (USE_MOCKS) {
    const { api_key, ...rest } = body.assistant
    mockState.settings = {
      retention: body.retention,
      changes_min_significance: body.changes_min_significance ?? mockState.settings.changes_min_significance ?? 'low',
      assistant: { ...mockState.settings.assistant, ...rest, api_key_set: api_key ? true : mockState.settings.assistant.api_key_set },
    }
    const a = mockState.settings.assistant
    mockState.assistantStatus = {
      available: a.enabled && (a.provider === 'mock' || a.api_key_set),
      provider: a.provider, model: a.model,
      reason: !a.enabled ? 'Assistant is disabled in Settings.' : a.provider === 'anthropic' && !a.api_key_set ? 'No Anthropic API key is configured. Add one in Settings or select the Mock provider.' : null,
    }
    return delay(mockState.settings, 400)
  }
  return apiSend<Settings>('PUT', '/settings', body)
}

// The significance floor from Settings, with the contract default when the backend
// does not report one. Never throws: pages call this before their main load.
export async function getChangesMinSignificance(): Promise<Significance> {
  try {
    const s = await getSettings()
    return s.changes_min_significance ?? 'low'
  } catch {
    return 'low'
  }
}
