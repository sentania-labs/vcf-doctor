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
