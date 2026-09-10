import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError, apiGet } from './client.ts'

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
