// Single place all API access goes through. Every api/* module uses these.
const BASE = '/api'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function parse<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`
    try {
      const body = await r.json()
      if (body && typeof body.detail === 'string') detail = body.detail
      else if (body && typeof body.message === 'string') detail = body.message
    } catch { /* body not json */ }
    throw new ApiError(r.status, detail)
  }
  if (r.status === 204) return undefined as T
  const text = await r.text()
  if (!text) return undefined as T
  return JSON.parse(text) as T
}

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`)
  return parse<T>(r)
}

export async function apiSend<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return parse<T>(r)
}

export function apiUrl(path: string): string {
  return `${BASE}${path}`
}
