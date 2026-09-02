import type { ConnectionCreate, ConnectionPublic, ConnectionTestResult, Schedule } from '@/types'
import { apiGet, apiSend } from './client'
import { USE_MOCKS, delay, mockEstate, mockState } from './mocks'

export function getConnections(): Promise<ConnectionPublic[]> {
  if (USE_MOCKS) return delay(mockState.estates.map(e => e.connection), 120)
  return apiGet<ConnectionPublic[]>('/connections')
}

export function createConnection(body: ConnectionCreate): Promise<ConnectionPublic> {
  if (USE_MOCKS) {
    const id = `vc${mockState.nextId++}`
    const conn: ConnectionPublic = { id, name: body.name, host: body.host, username: body.username, verify_tls: body.verify_tls, created_at: new Date().toISOString(), kind: 'vcenter' }
    mockState.estates.push({
      connection: conn,
      schedule: { connection_id: id, interval_minutes: body.interval_minutes, enabled: body.enabled, last_run: null, next_run: body.enabled ? new Date(Date.now() + body.interval_minutes * 60_000).toISOString() : null, last_status: null },
      resources: [], findings: [], snapshots: [], changes: [], changeLog: [], events: [], scans: [],
    })
    return delay(conn, 500)
  }
  return apiSend<ConnectionPublic>('POST', '/connections', body)
}

export function updateConnection(id: string, body: Partial<ConnectionCreate>): Promise<ConnectionPublic> {
  if (USE_MOCKS) {
    const e = mockEstate(id)[0]
    if (!e) return Promise.reject(new Error('Unknown connection'))
    e.connection = { ...e.connection, ...(body.name ? { name: body.name } : {}), ...(body.host ? { host: body.host } : {}), ...(body.username ? { username: body.username } : {}), ...(body.verify_tls !== undefined ? { verify_tls: body.verify_tls } : {}) }
    return delay(e.connection, 300)
  }
  return apiSend<ConnectionPublic>('PUT', `/connections/${encodeURIComponent(id)}`, body)
}

export function deleteConnection(id: string): Promise<void> {
  if (USE_MOCKS) {
    mockState.estates = mockState.estates.filter(e => e.connection.id !== id)
    return delay(undefined, 300)
  }
  return apiSend<void>('DELETE', `/connections/${encodeURIComponent(id)}`)
}

export function testConnection(id: string): Promise<ConnectionTestResult> {
  if (USE_MOCKS) return delay({ ok: true, message: 'Authenticated to vCenter', version: '9.0.1', build: '24805960' }, 1200)
  return apiSend<ConnectionTestResult>('POST', `/connections/${encodeURIComponent(id)}/test`)
}

// Test before the connection exists (Add dialog). Assumed shape: POST /connections/test with the create body.
export function testConnectionDraft(body: ConnectionCreate): Promise<ConnectionTestResult> {
  if (USE_MOCKS) {
    if (!body.host) return delay({ ok: false, message: 'Host is required' }, 300)
    return delay({ ok: true, message: `Authenticated to ${body.host}`, version: '9.0.1', build: '24805960' }, 1200)
  }
  return apiSend<ConnectionTestResult>('POST', '/connections/test', body)
}

export function getSchedule(id: string): Promise<Schedule> {
  if (USE_MOCKS) {
    const e = mockEstate(id)[0]
    return e ? delay(e.schedule, 100) : Promise.reject(new Error('Unknown connection'))
  }
  return apiGet<Schedule>(`/connections/${encodeURIComponent(id)}/schedule`)
}

export function updateSchedule(id: string, body: { interval_minutes: number; enabled: boolean }): Promise<Schedule> {
  if (USE_MOCKS) {
    const e = mockEstate(id)[0]
    if (!e) return Promise.reject(new Error('Unknown connection'))
    e.schedule = { ...e.schedule, ...body, next_run: body.enabled ? new Date(Date.now() + body.interval_minutes * 60_000).toISOString() : null }
    return delay(e.schedule, 300)
  }
  return apiSend<Schedule>('PUT', `/connections/${encodeURIComponent(id)}/schedule`, body)
}
