import { apiGet, apiSend } from './client'
import { USE_MOCKS, delay } from './mocks'
import { ApiError } from './client'

export interface AuthStatus { enabled: boolean; configured: boolean; authenticated: boolean }
interface OkResponse { ok: boolean }

// In-memory mock auth. Starts unconfigured so the first-run setup flow is visible.
// Kept in sessionStorage so a page reload during a mock session does not log you out.
interface MockAuth { configured: boolean; authenticated: boolean; password: string }
const MOCK_KEY = 'vcfdoctor.mockAuth'
function loadMock(): MockAuth {
  try { const raw = sessionStorage.getItem(MOCK_KEY); if (raw) return JSON.parse(raw) as MockAuth } catch { /* ignore */ }
  return { configured: false, authenticated: false, password: '' }
}
function saveMock(m: MockAuth) { try { sessionStorage.setItem(MOCK_KEY, JSON.stringify(m)) } catch { /* ignore */ } }
const mock = loadMock()

export function getAuthStatus(): Promise<AuthStatus> {
  if (USE_MOCKS) return delay({ enabled: true, configured: mock.configured, authenticated: mock.authenticated }, 120)
  return apiGet<AuthStatus>('/auth/status')
}

export async function setupPassword(password: string): Promise<void> {
  if (USE_MOCKS) {
    await delay(null, 300)
    if (mock.configured) throw new ApiError(409, 'password already configured')
    if (password.length < 8) throw new ApiError(400, 'password must be at least 8 characters')
    mock.configured = true; mock.authenticated = true; mock.password = password; saveMock(mock)
    return
  }
  await apiSend<OkResponse>('POST', '/auth/setup', { password })
}

export async function login(password: string): Promise<void> {
  if (USE_MOCKS) {
    await delay(null, 300)
    if (!mock.configured || password !== mock.password) throw new ApiError(401, 'invalid password')
    mock.authenticated = true; saveMock(mock)
    return
  }
  await apiSend<OkResponse>('POST', '/auth/login', { password })
}

export async function logout(): Promise<void> {
  if (USE_MOCKS) {
    await delay(null, 150)
    mock.authenticated = false; saveMock(mock)
    return
  }
  await apiSend<OkResponse>('POST', '/auth/logout')
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  if (USE_MOCKS) {
    await delay(null, 300)
    if (currentPassword !== mock.password) throw new ApiError(401, 'invalid password')
    if (newPassword.length < 8) throw new ApiError(400, 'password must be at least 8 characters')
    mock.password = newPassword; saveMock(mock)
    return
  }
  await apiSend<OkResponse>('POST', '/auth/change', { current_password: currentPassword, new_password: newPassword })
}
