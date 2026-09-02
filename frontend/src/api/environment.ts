import type { ChangeLogEntry, EnvironmentChanges, EnvironmentConnection, Finding, Significance, SignificanceCounts } from '@/types'
import { apiGet } from './client'
import { qs } from '@/lib/format'
import { SIGNIFICANCE_RANK } from './changes'
import { USE_MOCKS, delay, mockEstate, mockState } from './mocks'

export interface EnvironmentQuery {
  since?: string | null   // omitted: the backend uses the last scan cycle
  until?: string | null   // omitted: now
  minSignificance?: Significance | null
  limitPerConnection?: number | null
}

// GET /environment/changes: the persisted change log of every connection inside [since, until],
// with rolled-up counts. Connections with nothing in the window come back with has_data=false.
export function getEnvironmentChanges(query: EnvironmentQuery = {}): Promise<EnvironmentChanges> {
  if (USE_MOCKS) return delay(mockEnvironment(query), 300)
  return apiGet<EnvironmentChanges>(`/environment/changes${qs({
    since: query.since, until: query.until, min_significance: query.minSignificance, limit_per_connection: query.limitPerConnection,
  })}`)
}

function mockEnvironment(query: EnvironmentQuery): EnvironmentChanges {
  const untilMs = query.until ? new Date(query.until).getTime() : Date.now()
  const estates = mockEstate()
  const latest = estates.map(e => Math.max(...e.snapshots.filter(s => new Date(s.created_at).getTime() <= untilMs).map(s => new Date(s.created_at).getTime()))).filter(Number.isFinite)
  const sinceMs = query.since ? new Date(query.since).getTime() : latest.length ? Math.min(...latest) : untilMs - 86_400_000
  const floorSig = query.minSignificance ?? mockState.settings.changes_min_significance ?? 'low'
  const floor = SIGNIFICANCE_RANK[floorSig]
  const inWindow = (iso: string) => { const t = new Date(iso).getTime(); return t >= sinceMs && t <= untilMs }
  const connections: EnvironmentConnection[] = estates.map(e => {
    const rows: ChangeLogEntry[] = e.changeLog.filter(c => inWindow(c.observed_at)).sort((a, b) => b.observed_at.localeCompare(a.observed_at))
    const counts: SignificanceCounts = { high: 0, medium: 0, low: 0, total: 0 }
    for (const r of rows) counts[r.significance]++
    const visible = rows.filter(r => SIGNIFICANCE_RANK[r.significance] >= floor)
    const snaps = e.snapshots.filter(s => inWindow(s.created_at)).sort((a, b) => a.created_at.localeCompare(b.created_at))
    const hasData = snaps.length > 0 || rows.length > 0
    const appeared: Finding[] = hasData ? e.findings.slice(0, 2) : []
    return {
      connection_id: e.connection.id, name: e.connection.name, host: e.connection.host, kind: e.connection.kind,
      has_data: hasData, snapshots_in_window: snaps.length,
      counts: { high: floor <= 2 ? counts.high : 0, medium: floor <= 1 ? counts.medium : 0, low: floor <= 0 ? counts.low : 0, total: visible.length },
      changes: visible, truncated: false, pruned_snapshot_ids: [],
      findings: snaps.length >= 2 ? { baseline_snapshot_id: snaps[0].id, baseline_at: snaps[0].created_at, end_snapshot_id: snaps[snaps.length - 1].id, end_at: snaps[snaps.length - 1].created_at, appeared, cleared: [] } : null,
    }
  }).sort((a, b) => a.name.localeCompare(b.name))
  const changes: SignificanceCounts = { high: 0, medium: 0, low: 0, total: 0 }
  for (const c of connections) { changes.high += c.counts.high; changes.medium += c.counts.medium; changes.low += c.counts.low; changes.total += c.counts.total }
  const covered = connections.filter(c => c.has_data).length
  return {
    since: new Date(sinceMs).toISOString(), until: new Date(untilMs).toISOString(), window: query.since ? 'custom' : 'last_cycle', min_significance: floorSig,
    totals: { connections: connections.length, covered, no_data: connections.length - covered, changes, findings_appeared: connections.reduce((n, c) => n + (c.findings?.appeared.length ?? 0), 0), findings_cleared: 0, findings_compared: connections.filter(c => c.findings).length },
    connections,
  }
}
