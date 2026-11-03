import assert from 'node:assert/strict'
import { test } from 'node:test'
import { canStartScan, topBarStatus } from './topBarState.ts'

test('Scan Now requires a serving console and at least one connection', () => {
  assert.equal(canStartScan('checking', 1), true)
  assert.equal(canStartScan('down', 1), false)
  assert.equal(canStartScan('up', 0), false)
  assert.equal(canStartScan('up', 1, true), false)
})

test('during a database outage the top bar names the database, not the console', () => {
  assert.deepEqual(topBarStatus('down', [], true, true), { dot: 'error', label: 'Database unreachable' })
  assert.deepEqual(topBarStatus('up', ['ok'], true, true), { dot: 'error', label: 'Database unreachable' })
  assert.equal(topBarStatus('down', [], true).label, 'Console unavailable')
})
