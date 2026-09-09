import assert from 'node:assert/strict'
import { test } from 'node:test'
import { databaseHealthPresentation } from './databaseHealth.ts'

test('database health has healthy, unavailable, and cannot-tell states', () => {
  assert.equal(databaseHealthPresentation(true).label, 'Healthy')
  assert.equal(databaseHealthPresentation(false).label, 'Unavailable')
  const unknown = databaseHealthPresentation(null)
  assert.equal(unknown.label, 'Cannot tell')
  assert.match(unknown.message, /backend did not answer/)
  assert.doesNotMatch(unknown.message, /database is (down|unavailable|unreachable)/i)
})
