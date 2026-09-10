import assert from 'node:assert/strict'
import { test } from 'node:test'
import { classifyReadiness } from './backendHealth.ts'

test('readiness distinguishes available, database failure, and unknown responses', () => {
  assert.deepEqual(
    classifyReadiness({ status: 'ok', database: true }),
    { backend: 'up', backendError: null, databaseHealthy: true },
  )
  assert.deepEqual(
    classifyReadiness({ status: 'degraded', database: false }),
    { backend: 'down', backendError: '503 Service Unavailable', databaseHealthy: false },
  )
  assert.deepEqual(
    classifyReadiness({ status: 'degraded' }),
    { backend: 'down', backendError: '503 Service Unavailable', databaseHealthy: null },
  )
})
