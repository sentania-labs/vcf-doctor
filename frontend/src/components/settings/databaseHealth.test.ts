import assert from 'node:assert/strict'
import { test } from 'node:test'
import { databaseHealthPresentation } from './databaseHealth.ts'

test('database health has two states and makes no claim without an answer', () => {
  assert.equal(databaseHealthPresentation(true)?.label, 'Healthy')
  assert.deepEqual(databaseHealthPresentation(false), {
    label: 'Not healthy',
    tone: 'critical',
    message: 'The database is not reporting healthy. Check the server log for the reason.',
  })
  assert.equal(databaseHealthPresentation(null), null)
})
