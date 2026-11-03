import assert from 'node:assert/strict'
import { test } from 'node:test'
import { databaseOutage } from './databaseOutage.ts'

test('the outage is known from either the auth status or the readiness poll, and not before either speaks', () => {
  assert.equal(databaseOutage({ database: 'unavailable' }, null), true)
  assert.equal(databaseOutage({}, false), true)
  assert.equal(databaseOutage(null, false), true)
  assert.equal(databaseOutage({}, true), false)
  assert.equal(databaseOutage({}, null), false)
  assert.equal(databaseOutage(null, null), false)
})
