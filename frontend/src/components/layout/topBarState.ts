import type { BackendStatus } from '../../state/backendHealth.ts'
import type { ScanStatus } from '../../types/index.ts'

export interface TopBarStatus {
  dot: 'ok' | 'error' | 'running' | 'none'
  label: string
}

export function topBarStatus(
  backend: BackendStatus,
  scanStatuses: ScanStatus[],
  allSelected: boolean,
  databaseDown = false,
): TopBarStatus {
  if (databaseDown) return { dot: 'error', label: 'Database unreachable' }
  if (backend === 'down') return { dot: 'error', label: 'Console unavailable' }
  if (backend === 'checking') return { dot: 'running', label: 'Checking console' }
  if (scanStatuses.includes('running')) return { dot: 'running', label: 'Scanning' }
  if (scanStatuses.length > 0 && scanStatuses.every(status => status === 'ok')) {
    return { dot: 'ok', label: 'Connected' }
  }
  if (scanStatuses.includes('error')) {
    return {
      dot: 'error',
      label: allSelected ? 'A connection is failing' : 'Last scan failed',
    }
  }
  return { dot: 'none', label: 'Not scanned yet' }
}

export function canStartScan(backend: BackendStatus, connectionCount: number, databaseDown = false): boolean {
  return backend !== 'down' && !databaseDown && connectionCount > 0
}
