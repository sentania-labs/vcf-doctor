import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import type { ConnectionPublic, ScanRun } from '@/types'
import { getConnections, getHealth, getScans, triggerScan } from '@/api'
import { useInterval } from '@/hooks/useAsync'

const STORAGE_KEY = 'vcfdoctor.connection'
export const ALL = 'all'

export type BackendStatus = 'checking' | 'up' | 'down'

interface AppState {
  connections: ConnectionPublic[]
  connectionsLoading: boolean
  selectedId: string            // ALL or a connection id
  connectionId: string | null   // null when ALL
  selected: ConnectionPublic | null
  setSelectedId: (id: string) => void
  backend: BackendStatus
  backendError: string | null
  scans: ScanRun[]
  lastScan: string | null
  scanning: boolean
  scanNow: () => Promise<void>
  scanError: string | null
  refreshKey: number
  refreshAll: () => void
  reloadConnections: () => Promise<void>
}

const Ctx = createContext<AppState | null>(null)

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [connections, setConnections] = useState<ConnectionPublic[]>([])
  const [connectionsLoading, setConnectionsLoading] = useState(true)
  const [selectedId, setSelectedIdState] = useState<string>(() => {
    try { return localStorage.getItem(STORAGE_KEY) ?? ALL } catch { return ALL }
  })
  const [backend, setBackend] = useState<BackendStatus>('checking')
  const [backendError, setBackendError] = useState<string | null>(null)
  const [scans, setScans] = useState<ScanRun[]>([])
  const [scanError, setScanError] = useState<string | null>(null)
  const [scanning, setScanning] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)

  const setSelectedId = useCallback((id: string) => {
    setSelectedIdState(id)
    try { localStorage.setItem(STORAGE_KEY, id) } catch { /* private mode */ }
  }, [])

  const connectionId = selectedId === ALL ? null : selectedId
  const selected = useMemo(() => connections.find(c => c.id === selectedId) ?? null, [connections, selectedId])

  const checkBackend = useCallback(async () => {
    try {
      await getHealth()
      setBackend('up'); setBackendError(null)
    } catch (e) {
      setBackend('down'); setBackendError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  const reloadConnections = useCallback(async () => {
    try {
      const list = await getConnections()
      setConnections(list)
    } catch { /* backend banner covers it */ } finally { setConnectionsLoading(false) }
  }, [])

  const reloadScans = useCallback(async () => {
    try {
      const list = await getScans(connectionId)
      setScans(list)
      setScanning(list.some(s => s.status === 'running'))
    } catch { /* ignore */ }
  }, [connectionId])

  useEffect(() => { void checkBackend(); void reloadConnections() }, [checkBackend, reloadConnections])
  useEffect(() => { void reloadScans() }, [reloadScans, refreshKey])

  // Backend heartbeat; faster while down so recovery is noticed quickly.
  useInterval(() => { void checkBackend() }, backend === 'down' ? 5000 : 20000)
  // Poll scans while one is running so the top bar and pages refresh when it lands.
  useInterval(() => {
    void (async () => {
      const before = scans.filter(s => s.status === 'running').map(s => s.id).join(',')
      await reloadScans()
      const list = await getScans(connectionId).catch(() => null)
      if (list && before && !list.some(s => s.status === 'running')) setRefreshKey(k => k + 1)
    })()
  }, scanning ? 2000 : 30000)

  // If the stored selection no longer exists, fall back to All.
  useEffect(() => {
    if (!connectionsLoading && selectedId !== ALL && connections.length > 0 && !connections.some(c => c.id === selectedId)) setSelectedId(ALL)
  }, [connections, connectionsLoading, selectedId, setSelectedId])

  const scanNow = useCallback(async () => {
    setScanError(null)
    setScanning(true)
    try {
      await triggerScan(connectionId)
      await reloadScans()
      setRefreshKey(k => k + 1)
    } catch (e) {
      setScanError(e instanceof Error ? e.message : String(e))
      setScanning(false)
    }
  }, [connectionId, reloadScans])

  const lastScan = useMemo(() => {
    const finished = scans.filter(s => s.finished && s.status === 'ok').map(s => s.finished as string).sort()
    return finished.at(-1) ?? null
  }, [scans])

  const value: AppState = {
    connections, connectionsLoading, selectedId, connectionId, selected, setSelectedId,
    backend, backendError, scans, lastScan, scanning, scanNow, scanError,
    refreshKey, refreshAll: () => setRefreshKey(k => k + 1), reloadConnections,
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAppState(): AppState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAppState outside provider')
  return v
}
