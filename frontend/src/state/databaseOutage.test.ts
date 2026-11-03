import assert from 'node:assert/strict'
import { test } from 'node:test'
import { databaseOutage, outageBehindUnauthorized } from './databaseOutage.ts'

test('the outage is known from either the auth status or the readiness poll, and not before either speaks', () => {
  assert.equal(databaseOutage({ database: 'unavailable' }, null), true)
  assert.equal(databaseOutage({}, false), true)
  assert.equal(databaseOutage(null, false), true)
  assert.equal(databaseOutage({}, true), false)
  assert.equal(databaseOutage({}, null), false)
  assert.equal(databaseOutage(null, null), false)
})

test('a 401 during an outage keeps the console; otherwise, or if the status cannot be had, it means signed out', async () => {
  const down = { enabled: true, authenticated: false, database: 'unavailable' as const }
  assert.equal(await outageBehindUnauthorized(async () => down), down)
  assert.equal(await outageBehindUnauthorized(async () => ({ enabled: true, authenticated: false })), null)
  assert.equal(await outageBehindUnauthorized(async () => { throw new Error('unreachable') }), null)
})
