import type { ApiToken, CreatedApiToken, ServiceAccount, ServiceAccountList, ServiceAccountTokens } from '@/types'
import { apiGet, apiSend } from './client'
import { USE_MOCKS, delay } from './mocks'

const day = 86_400_000
let mockAccounts: ServiceAccount[] = [{
  id: 'sa1', name: 'ci-runner', description: 'nightly export job', role: 'admin',
  created_at: new Date(Date.now() - 9 * day).toISOString(), created_by: 'operator password',
  disabled_at: null, disabled_by: null, active_tokens: 1,
}]
let mockTokens: Record<string, ApiToken[]> = {
  sa1: [{
    id: 'sat1', name: 'rotation-1', hint: 'vcfd_sat_Hq2m', role: 'admin',
    created_at: new Date(Date.now() - 9 * day).toISOString(), expires_at: new Date(Date.now() + 80 * day).toISOString(),
    last_used_at: new Date(Date.now() - 600_000).toISOString(), revoked_at: null, revoked_reason: null,
  }],
}

const base = '/service-accounts'
const path = (id: string) => `${base}/${encodeURIComponent(id)}`

export function listServiceAccounts(): Promise<ServiceAccountList> {
  if (USE_MOCKS) return delay({ accounts: mockAccounts, roles: ['admin'], default_days: 30 }, 120)
  return apiGet<ServiceAccountList>(base)
}

export function createServiceAccount(name: string, description: string, role: string): Promise<ServiceAccount> {
  if (USE_MOCKS) {
    const a: ServiceAccount = {
      id: Math.random().toString(16).slice(2, 10), name, description, role, created_at: new Date().toISOString(),
      created_by: 'operator password', disabled_at: null, disabled_by: null, active_tokens: 0,
    }
    mockAccounts = [...mockAccounts, a]
    return delay(a, 200)
  }
  return apiSend<ServiceAccount>('POST', base, { name, description, role })
}

export function setServiceAccountDisabled(id: string, disabled: boolean): Promise<ServiceAccount> {
  if (USE_MOCKS) {
    mockAccounts = mockAccounts.map(a => (a.id === id ? { ...a, disabled_at: disabled ? new Date().toISOString() : null } : a))
    return delay(mockAccounts.find(a => a.id === id)!, 200)
  }
  return apiSend<ServiceAccount>('POST', `${path(id)}/${disabled ? 'disable' : 'enable'}`)
}

export function listServiceAccountTokens(id: string): Promise<ServiceAccountTokens> {
  if (USE_MOCKS) return delay({ tokens: mockTokens[id] ?? [], default_days: 30 }, 120)
  return apiGet<ServiceAccountTokens>(`${path(id)}/tokens`)
}

export function createServiceAccountToken(id: string, name: string, expiresInDays: number): Promise<CreatedApiToken> {
  if (USE_MOCKS) {
    const info: ApiToken = {
      id: Math.random().toString(16).slice(2, 10), name, hint: 'vcfd_sat_Zq9k', role: 'admin',
      created_at: new Date().toISOString(), expires_at: new Date(Date.now() + expiresInDays * day).toISOString(),
      last_used_at: null, revoked_at: null, revoked_reason: null,
    }
    mockTokens = { ...mockTokens, [id]: [info, ...(mockTokens[id] ?? [])] }
    return delay({ token: 'vcfd_sat_Zq9kMockOnlyNotARealSecret0000000000000', info }, 300)
  }
  return apiSend<CreatedApiToken>('POST', `${path(id)}/tokens`, { name, expires_in_days: expiresInDays })
}

export async function revokeServiceAccountToken(id: string, tokenId: string): Promise<void> {
  if (USE_MOCKS) {
    mockTokens = { ...mockTokens, [id]: (mockTokens[id] ?? []).map(t => (t.id === tokenId ? { ...t, revoked_at: new Date().toISOString(), revoked_reason: 'revoked' } : t)) }
    await delay(null, 200)
    return
  }
  await apiSend<{ ok: boolean }>('DELETE', `${path(id)}/tokens/${encodeURIComponent(tokenId)}`)
}
