import test from 'node:test'
import assert from 'node:assert/strict'
import { assistantBadge } from './assistantStatus.ts'

const configured = { available: true, provider: 'anthropic', model: 'model', reason: null }
const unavailable = { ...configured, available: false, reason: 'No key' }

test('the badge only claims configuration, including after a failed model lookup', () => {
  assert.deepEqual(assistantBadge(configured, false, null), { label: 'Configured', tone: 'ok' })
  assert.deepEqual(assistantBadge(unavailable, false, null), { label: 'Unavailable', tone: 'warning' })
  assert.deepEqual(assistantBadge(configured, true, null), { label: 'Checking', tone: 'neutral' })
  assert.deepEqual(assistantBadge(configured, false, new Error('offline')), { label: 'Status unknown', tone: 'warning' })
  assert.equal(assistantBadge(null, false, null), null)
})
