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
