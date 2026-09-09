import assert from 'node:assert/strict'
import { test } from 'node:test'
import { classifyReadiness } from './backendHealth.ts'

test('readiness distinguishes startup, database failure, and an unknown response', () => {
  assert.deepEqual(
    classifyReadiness({ status: 'degraded', database: true, startup_complete: false }),
    { backend: 'starting', backendError: null, databaseHealthy: true },
  )
  assert.deepEqual(
    classifyReadiness({ status: 'degraded', database: false, startup_complete: false }),
    { backend: 'down', backendError: '503 Service Unavailable', databaseHealthy: false },
  )
  assert.deepEqual(
    classifyReadiness({ status: 'degraded' }),
    { backend: 'down', backendError: '503 Service Unavailable', databaseHealthy: null },
  )
})

test('readiness only reports the console up from a complete healthy response', () => {
  assert.deepEqual(
    classifyReadiness({ status: 'ok', database: true, startup_complete: true }),
    { backend: 'up', backendError: null, databaseHealthy: true },
  )
})
