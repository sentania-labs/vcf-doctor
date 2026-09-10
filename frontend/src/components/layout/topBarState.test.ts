import assert from 'node:assert/strict'
import { test } from 'node:test'
import { canStartScan, topBarStatus } from './topBarState.ts'

test('maintenance informs without gating scans or hiding progress', () => {
  for (const backend of ['starting', 'maintenance'] as const) {
    assert.equal(canStartScan(backend, 1), true)
    assert.deepEqual(topBarStatus(backend, ['running'], false), {
      dot: 'running',
      label: 'Scanning',
    })
  }
  assert.equal(canStartScan('checking', 1), true)
  assert.equal(canStartScan('down', 1), false)
  assert.equal(canStartScan('up', 0), false)
})
