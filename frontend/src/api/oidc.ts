import type { OidcSettings, OidcTest, OidcValues } from '@/types'
import { apiGet, apiSend } from './client'
import { USE_MOCKS, delay } from './mocks'

// Mock: single sign-on not set up, as on a fresh install.
const EMPTY: OidcValues = { issuer: '', client_id: '', redirect_uri: '', scopes: '', groups_claim: '', admin_groups: [] }
const NOT_PINNED = { issuer: false, client_id: false, redirect_uri: false, scopes: false, groups_claim: false, admin_groups: false }
let mockOidc: OidcSettings = {
  stored: EMPTY, effective: EMPTY, pinned: NOT_PINNED,
  defaults: { scopes: 'openid profile email', groups_claim: 'groups' },
  configured: false, problems: ['an issuer is required', 'a client ID is required', 'at least one admin group is required'],
  callback_url: 'http://localhost:8000/api/auth/oidc/callback', client_secret: false, auth_enabled: true,
}

export function getOidcSettings(): Promise<OidcSettings> {
  if (USE_MOCKS) return delay(mockOidc, 120)
  return apiGet<OidcSettings>('/settings/oidc')
}

export function updateOidcSettings(values: OidcValues): Promise<OidcSettings> {
  if (USE_MOCKS) {
    const problems: string[] = []
    if (!values.issuer) problems.push('an issuer is required')
    if (!values.client_id) problems.push('a client ID is required')
    if (values.admin_groups.length === 0) problems.push('at least one admin group is required')
    mockOidc = { ...mockOidc, stored: values, effective: values, configured: problems.length === 0, problems }
    return delay(mockOidc, 300)
  }
  return apiSend<OidcSettings>('PUT', '/settings/oidc', values)
}

export function testOidc(): Promise<OidcTest> {
  if (USE_MOCKS) return delay({ ok: true, error: null, issuer: mockOidc.effective.issuer, end_session: true, pkce: true, secret: false }, 400)
  return apiSend<OidcTest>('POST', '/settings/oidc/test')
}
