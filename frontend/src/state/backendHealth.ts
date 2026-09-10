export type BackendStatus = 'checking' | 'maintenance' | 'up' | 'down'

export interface ReadinessState {
  status?: unknown
  database?: unknown
  startup_failures?: unknown
}

export interface BackendHealth {
  backend: BackendStatus
  backendError: string | null
  databaseHealthy: boolean | null
  maintenanceFailures: string[]
}

export function classifyReadiness(readiness: ReadinessState): BackendHealth {
  const databaseHealthy = typeof readiness.database === 'boolean' ? readiness.database : null
  const maintenanceFailures = Array.isArray(readiness.startup_failures)
    ? readiness.startup_failures.filter(
      (identifier): identifier is string => typeof identifier === 'string' && identifier.length > 0,
    )
    : []
  if (databaseHealthy === true && readiness.status === 'ok') {
    return {
      backend: maintenanceFailures.length > 0 ? 'maintenance' : 'up',
      backendError: null,
      databaseHealthy,
      maintenanceFailures,
    }
  }
  return {
    backend: 'down',
    backendError: '503 Service Unavailable',
    databaseHealthy,
    maintenanceFailures: [],
  }
}
