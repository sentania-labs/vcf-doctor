export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return 'never'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return 'unknown'
  const diff = Math.round((now - t) / 1000)
  const future = diff < 0
  const s = Math.abs(diff)
  let out: string
  if (s < 5) return future ? 'in a moment' : 'just now'
  if (s < 60) out = `${s}s`
  else if (s < 3600) out = `${Math.floor(s / 60)}m`
  else if (s < 86400) out = `${Math.floor(s / 3600)}h`
  else out = `${Math.floor(s / 86400)}d`
  return future ? `in ${out}` : `${out} ago`
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function formatValue(v: unknown): string {
  if (v === null || v === undefined) return 'none'
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2)
  if (typeof v === 'string') return v
  if (Array.isArray(v)) return v.length === 0 ? '[]' : v.map(formatValue).join(', ')
  try { return JSON.stringify(v) } catch { return String(v) }
}

// Property keys whose numeric values are byte counts (freeSpace, capacity, memoryBytes, ...).
export function isByteKey(k: string): boolean {
  if (/(GB|MB|Pct)$/.test(k)) return false // capacityGB, memoryMB, usedPct carry their own unit
  return /(freeSpace|capacity|bytes)$/i.test(k) || /^(freeSpace|capacity)/i.test(k)
}

export function formatBytes(n: number): string {
  if (!Number.isFinite(n)) return String(n)
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']
  let v = Math.abs(n), i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  const digits = i === 0 ? 0 : v >= 100 ? 0 : v >= 10 ? 1 : 2
  return `${n < 0 ? '-' : ''}${v.toFixed(digits)} ${units[i]}`
}

export function humanKey(k: string): string {
  return k
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/^./, c => c.toUpperCase())
}

export function pct(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || Number.isNaN(n)) return 'n/a'
  return `${n.toFixed(digits)}%`
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

// Seconds of uptime as "41d 3h" (or "3h 12m" under a day, "12m" under an hour).
export function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return String(seconds)
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

// MHz as GHz once it reads better ("2.40 GHz"), raw MHz below 1000.
export function formatMhz(mhz: number): string {
  if (!Number.isFinite(mhz)) return String(mhz)
  return mhz >= 1000 ? `${(mhz / 1000).toFixed(2)} GHz` : `${mhz} MHz`
}

// MiB as a byte string via formatBytes (memoryMB, memReservationMB).
export function formatMiB(mib: number): string {
  return formatBytes(mib * 1024 * 1024)
}

// Property keys that carry ISO timestamps.
export function isTimeKey(k: string): boolean {
  return /(Time|At)$/.test(k) || k === 'bootTime'
}

// Property keys that carry MiB counts.
export function isMiBKey(k: string): boolean {
  return /MB$/.test(k)
}

// Property keys that carry MHz.
export function isMhzKey(k: string): boolean {
  return /Mhz$/i.test(k)
}

// Property keys that carry seconds.
export function isSecondsKey(k: string): boolean {
  return /Seconds$/.test(k)
}

// Human rendering of a single scalar property value, keyed so units come out right.
export function formatProperty(k: string, v: unknown): string {
  if (v === null || v === undefined) return 'none'
  if (typeof v === 'number') {
    if (isByteKey(k)) return formatBytes(v)
    if (isMiBKey(k)) return formatMiB(v)
    if (/GB$/.test(k)) return `${formatValue(v)} GB`
    if (isMhzKey(k)) return formatMhz(v)
    if (isSecondsKey(k)) return formatUptime(v)
    if (/Pct$/.test(k)) return `${v}%`
    return formatValue(v)
  }
  if (typeof v === 'string' && isTimeKey(k)) {
    const d = formatDateTime(v)
    return d && d !== v ? `${d} (${relativeTime(v)})` : v
  }
  return formatValue(v)
}

/* ---------- Day grouping and time-range presets (Snapshots, Changes timeline, Events) ---------- */

// Local calendar day key for an ISO time ("2026-09-01"). Groups lists under date headers.
export function dayKey(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// "Today", "Yesterday", else "Mon, Sep 1" (with the year once it differs from the current one).
export function dayLabel(iso: string, now: number = Date.now()): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const key = dayKey(iso)
  if (key === dayKey(new Date(now).toISOString())) return 'Today'
  if (key === dayKey(new Date(now - 86_400_000).toISOString())) return 'Yesterday'
  const sameYear = d.getFullYear() === new Date(now).getFullYear()
  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }) })
}

export interface DayGroup<T> { key: string; label: string; items: T[] }
// Groups items (already in display order) under their local day, keeping first-seen order of days.
export function groupByDay<T>(items: T[], time: (t: T) => string): Array<DayGroup<T>> {
  const out: Array<DayGroup<T>> = []
  const idx = new Map<string, number>()
  for (const it of items) {
    const iso = time(it)
    const key = dayKey(iso)
    let i = idx.get(key)
    if (i === undefined) { i = out.length; idx.set(key, i); out.push({ key, label: dayLabel(iso), items: [] }) }
    out[i].items.push(it)
  }
  return out
}

export type RangePreset = '1h' | '24h' | '7d'
export const RANGE_PRESETS: Array<{ value: RangePreset; label: string; ms: number }> = [
  { value: '1h', label: '1 h', ms: 3_600_000 },
  { value: '24h', label: '24 h', ms: 86_400_000 },
  { value: '7d', label: '7 d', ms: 7 * 86_400_000 },
]
// ISO "since" for a preset, rounded to the minute so re-renders inside the same minute reuse the query key.
export function sinceFor(preset: RangePreset, now: number = Date.now()): string {
  const ms = RANGE_PRESETS.find(p => p.value === preset)?.ms ?? 86_400_000
  const t = now - ms
  return new Date(t - (t % 60_000)).toISOString()
}
