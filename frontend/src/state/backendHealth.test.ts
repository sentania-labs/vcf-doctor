import assert from 'node:assert/strict'
import { test } from 'node:test'
import { classifyReadiness } from './backendHealth.ts'

test('readiness distinguishes available, database failure, and unknown responses', () => {
  assert.deepEqual(
    classifyReadiness({ status: 'ok', database: true, startup_failures: [] }),
    { backend: 'up', backendError: null, databaseHealthy: true, maintenanceFailures: [] },
  )
  assert.deepEqual(
    classifyReadiness({ status: 'degraded', database: false }),
    { backend: 'down', backendError: '503 Service Unavailable', databaseHealthy: false, maintenanceFailures: [] },
  )
  assert.deepEqual(
    classifyReadiness({ status: 'degraded' }),
    { backend: 'down', backendError: '503 Service Unavailable', databaseHealthy: null, maintenanceFailures: [] },
  )
})

test('a failed maintenance step is a notice with stable identifiers', () => {
  assert.deepEqual(
    classifyReadiness({
      status: 'ok',
      database: true,
      startup_failures: ['vault_rekey'],
    }),
    {
      backend: 'maintenance',
      backendError: null,
      databaseHealthy: true,
      maintenanceFailures: ['vault_rekey'],
    },
  )
})
