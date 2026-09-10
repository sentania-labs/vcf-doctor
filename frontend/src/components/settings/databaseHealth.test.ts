import assert from 'node:assert/strict'
import { test } from 'node:test'
import { databaseHealthPresentation } from './databaseHealth.ts'

test('database health has two states and makes no claim without an answer', () => {
  assert.deepEqual(databaseHealthPresentation(true), {
    label: 'Healthy',
    tone: 'ok',
    message: 'The database connection is not editable here.',
  })
  assert.deepEqual(databaseHealthPresentation(false), {
    label: 'Not healthy',
    tone: 'critical',
    message: 'The database is not reporting healthy. Check the server log for the reason.',
  })
  assert.equal(databaseHealthPresentation(null), null)
})
