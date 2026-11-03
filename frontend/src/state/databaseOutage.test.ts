import assert from 'node:assert/strict'
import { test } from 'node:test'
import { databaseOutage } from './databaseOutage.ts'

test('the outage is known from either the auth status or the readiness poll, and not before either speaks', () => {
  assert.equal(databaseOutage({ database: 'unavailable' }, null, 'up'), true)
  assert.equal(databaseOutage({}, false, 'up'), true)
  assert.equal(databaseOutage(null, false, 'up'), true)
  assert.equal(databaseOutage({}, true, 'up'), false)
  assert.equal(databaseOutage({}, null, 'up'), false)
  assert.equal(databaseOutage(null, null, 'up'), false)
})

test('a console that cannot be reached is not a database outage, however stale the auth status', () => {
  assert.equal(databaseOutage({ database: 'unavailable' }, null, 'down'), false)
  assert.equal(databaseOutage({ database: 'unavailable' }, false, 'down'), true)
  assert.equal(databaseOutage({ database: 'unavailable' }, null, 'checking'), true)
})
