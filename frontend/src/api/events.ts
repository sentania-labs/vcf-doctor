import type { Event, EventCategory } from '@/types'
import { apiGet } from './client'
import { qs } from '@/lib/format'
import { USE_MOCKS, delay, mockEstate } from './mocks'

export interface EventQuery {
  connectionId?: string | null
  since?: string | null
  until?: string | null
  resourceId?: string | null
  category?: EventCategory | null
  q?: string | null
  limit?: number | null
}

// GET /events: vCenter events and tasks the collector stored, newest first.
// The backend defaults to the last 24 h and a limit of 500 when since/until/limit are omitted.
export function getEvents(query: EventQuery = {}): Promise<Event[]> {
  if (USE_MOCKS) {
    const sinceMs = query.since ? new Date(query.since).getTime() : Date.now() - 86_400_000
    const untilMs = query.until ? new Date(query.until).getTime() : Number.POSITIVE_INFINITY
    const needle = (query.q ?? '').trim().toLowerCase()
    const all = mockEstate(query.connectionId).flatMap(e => e.events)
      .filter(e => { const t = new Date(e.time).getTime(); return t >= sinceMs && t <= untilMs })
      .filter(e => !query.resourceId || e.resource_id === query.resourceId)
      .filter(e => !query.category || e.category === query.category)
      .filter(e => !needle || `${e.message} ${e.type} ${e.user ?? ''} ${e.resource_name ?? ''}`.toLowerCase().includes(needle))
      .sort((a, b) => b.time.localeCompare(a.time))
    return delay(all.slice(0, query.limit ?? 500), 220)
  }
  return apiGet<Event[]>(`/events${qs({
    connection_id: query.connectionId, since: query.since, until: query.until, resource_id: query.resourceId,
    category: query.category, q: query.q?.trim() || undefined, limit: query.limit,
  })}`)
}
