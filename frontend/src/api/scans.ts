import type { ScanRun } from '@/types'
import { apiGet, apiSend } from './client'
import { qs } from '@/lib/format'
import { USE_MOCKS, delay, mockEstate, mockState } from './mocks'

export function getScans(connectionId?: string | null): Promise<ScanRun[]> {
  if (USE_MOCKS) return delay(mockEstate(connectionId).flatMap(e => e.scans), 120)
  return apiGet<ScanRun[]>(`/scans${qs({ connection_id: connectionId })}`)
}

export function triggerScan(connectionId?: string | null): Promise<ScanRun[]> {
  if (USE_MOCKS) {
    const runs: ScanRun[] = []
    for (const e of mockEstate(connectionId)) {
      const started = new Date().toISOString()
      const run: ScanRun = { id: `scan-${e.connection.id}-${mockState.nextId++}`, connection_id: e.connection.id, started, finished: null, status: 'running', error: null, snapshot_id: null, trigger: 'manual' }
      e.scans.unshift(run)
      runs.push(run)
      setTimeout(() => {
        run.status = 'ok'
        run.finished = new Date().toISOString()
        const snap = { id: `snap-${e.connection.id}-${mockState.nextId++}`, created_at: run.finished, label: 'Manual scan', connection_id: e.connection.id, scheduled: false, resource_count: e.resources.length }
        run.snapshot_id = snap.id
        e.snapshots.unshift(snap)
        e.schedule.last_run = run.finished
        e.schedule.last_status = 'ok'
      }, 2500)
    }
    return delay(runs, 200)
  }
  return apiSend<ScanRun[]>('POST', '/scan', connectionId ? { connection_id: connectionId } : {})
}
