import assert from 'node:assert/strict'
import { test } from 'node:test'
import { canStartScan } from './topBarState.ts'

test('Scan Now requires a serving console and at least one connection', () => {
  assert.equal(canStartScan('checking', 1), true)
  assert.equal(canStartScan('down', 1), false)
  assert.equal(canStartScan('up', 0), false)
})
