import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError, DATABASE_UNAVAILABLE_EVENT, UNAUTHENTICATED_EVENT, apiGet } from './client.ts'

test('readiness can consume a degraded response without weakening other requests', async t => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  globalThis.fetch = async () => new Response(JSON.stringify({
    status: 'degraded', version: 'dev', scheduler: false, database: true,
  }), { status: 503, statusText: 'Service Unavailable' })

  const readiness = await apiGet<{ status: string; database: boolean }>('/health/ready', [503])
  assert.deepEqual(readiness, { status: 'degraded', version: 'dev', scheduler: false, database: true })
  await assert.rejects(
    apiGet('/settings'),
    (error: unknown) => error instanceof ApiError && error.status === 503,
  )
})

test('a 503 that says the database is unavailable is announced, and is not a signed-out session', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = (globalThis as { window?: unknown }).window
  const target = new EventTarget()
  ;(globalThis as { window?: unknown }).window = target
  t.after(() => { globalThis.fetch = originalFetch; (globalThis as { window?: unknown }).window = originalWindow })
  const body = { enabled: true, authenticated: false, database: 'unavailable' }
  globalThis.fetch = async () => new Response(JSON.stringify(body), { status: 503, statusText: 'Service Unavailable' })
  const seen: string[] = []
  for (const name of [DATABASE_UNAVAILABLE_EVENT, UNAUTHENTICATED_EVENT]) {
    target.addEventListener(name, e => seen.push(`${name}:${JSON.stringify((e as CustomEvent).detail ?? null)}`))
  }

  await assert.rejects(apiGet('/settings'), (error: unknown) => error instanceof ApiError && error.status === 503)
  assert.deepEqual(seen, [`${DATABASE_UNAVAILABLE_EVENT}:${JSON.stringify(body)}`])

  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'busy' }), { status: 503 })
  seen.length = 0
  await assert.rejects(apiGet('/settings'))
  assert.deepEqual(seen, [])
})
