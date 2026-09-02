import type { TrustedProxies } from '@/types'
import { apiGet, apiSend } from './client'
import { USE_MOCKS, delay } from './mocks'

// Mock: behaves like a fresh install (nothing trusted, no env override).
let mockProxies: TrustedProxies = { trusted_proxies: [], source: 'settings', stored: [], env_problem: null, peer: '10.42.0.7', peer_trusted: false, ignored_forwarded_headers: true, scheme: 'http' }

export function getTrustedProxies(): Promise<TrustedProxies> {
  if (USE_MOCKS) return delay(mockProxies, 120)
  return apiGet<TrustedProxies>('/settings/trusted-proxies')
}

export function updateTrustedProxies(entries: string[]): Promise<TrustedProxies> {
  if (USE_MOCKS) {
    const clean = entries.map(e => e.trim()).filter(Boolean)
    mockProxies = { ...mockProxies, trusted_proxies: clean, stored: clean, peer_trusted: clean.length > 0, ignored_forwarded_headers: clean.length === 0 }
    return delay(mockProxies, 300)
  }
  return apiSend<TrustedProxies>('PUT', '/settings/trusted-proxies', { trusted_proxies: entries })
}
