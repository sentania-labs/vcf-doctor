export type BackendStatus = 'checking' | 'starting' | 'maintenance' | 'up' | 'down'

export interface ReadinessState {
  status?: unknown
  database?: unknown
  startup_complete?: unknown
  startup_failures?: unknown
}

export interface BackendHealth {
  backend: BackendStatus
  backendError: string | null
  databaseHealthy: boolean | null
}

export function classifyReadiness(readiness: ReadinessState): BackendHealth {
  const databaseHealthy = typeof readiness.database === 'boolean' ? readiness.database : null
  if (databaseHealthy === true && readiness.startup_complete === false) {
    if (
      Array.isArray(readiness.startup_failures)
      && readiness.startup_failures.some(identifier => typeof identifier === 'string' && identifier)
    ) {
      return { backend: 'maintenance', backendError: null, databaseHealthy }
    }
    return { backend: 'starting', backendError: null, databaseHealthy }
  }
  if (databaseHealthy === true && readiness.startup_complete === true && readiness.status === 'ok') {
    return { backend: 'up', backendError: null, databaseHealthy }
  }
  return { backend: 'down', backendError: '503 Service Unavailable', databaseHealthy }
}
