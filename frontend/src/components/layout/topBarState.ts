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
): TopBarStatus {
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

export function canStartScan(backend: BackendStatus, connectionCount: number): boolean {
  return backend !== 'down' && connectionCount > 0
}
