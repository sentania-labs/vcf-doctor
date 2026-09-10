import assert from 'node:assert/strict'
import { test } from 'node:test'
import { databaseHealthPresentation } from './databaseHealth.ts'

test('database health has two states and makes no claim without an answer', () => {
  assert.equal(databaseHealthPresentation(true)?.label, 'Healthy')
  assert.equal(databaseHealthPresentation(false)?.label, 'Unavailable')
  assert.equal(databaseHealthPresentation(null), null)
})
