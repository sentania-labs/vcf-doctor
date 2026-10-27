import type { ApiToken, ApiTokenList, CreatedApiToken } from '@/types'
import { apiGet, apiSend } from './client'
import { USE_MOCKS, delay } from './mocks'

// Mock: one token already made, so the list is not empty on first sight.
const day = 86_400_000
let mockTokens: ApiToken[] = [{
  id: 'mock1', name: 'nightly export', hint: 'vcfd_pat_aB3x', role: 'admin',
  created_at: new Date(Date.now() - 3 * day).toISOString(), expires_at: new Date(Date.now() + 27 * day).toISOString(),
  last_used_at: new Date(Date.now() - 3600_000).toISOString(), revoked_at: null, revoked_reason: null,
}]

export function listApiTokens(): Promise<ApiTokenList> {
  if (USE_MOCKS) return delay({ tokens: mockTokens, max_days: 365, default_days: 30 }, 120)
  return apiGet<ApiTokenList>('/tokens')
}

export function createApiToken(name: string, expiresInDays: number): Promise<CreatedApiToken> {
  if (USE_MOCKS) {
    const info: ApiToken = {
      id: Math.random().toString(16).slice(2, 10), name, hint: 'vcfd_pat_Zq9k', role: 'admin',
      created_at: new Date().toISOString(), expires_at: new Date(Date.now() + expiresInDays * day).toISOString(),
      last_used_at: null, revoked_at: null, revoked_reason: null,
    }
    mockTokens = [info, ...mockTokens]
    return delay({ token: 'vcfd_pat_Zq9kMockOnlyNotARealSecret0000000000000', info }, 300)
  }
  return apiSend<CreatedApiToken>('POST', '/tokens', { name, expires_in_days: expiresInDays })
}

export async function revokeApiToken(id: string): Promise<void> {
  if (USE_MOCKS) {
    mockTokens = mockTokens.map(t => (t.id === id ? { ...t, revoked_at: new Date().toISOString(), revoked_reason: 'revoked' } : t))
    await delay(null, 200)
    return
  }
  await apiSend<{ ok: boolean }>('DELETE', `/tokens/${encodeURIComponent(id)}`)
}
