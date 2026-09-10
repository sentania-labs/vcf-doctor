export type BackendStatus = 'checking' | 'up' | 'down'

export interface ReadinessState {
  status?: unknown
  database?: unknown
}

export interface BackendHealth {
  backend: BackendStatus
  backendError: string | null
  databaseHealthy: boolean | null
}

export function classifyReadiness(readiness: ReadinessState): BackendHealth {
  const databaseHealthy = typeof readiness.database === 'boolean' ? readiness.database : null
  if (databaseHealthy === true && readiness.status === 'ok') {
    return { backend: 'up', backendError: null, databaseHealthy }
  }
  return { backend: 'down', backendError: '503 Service Unavailable', databaseHealthy }
}
