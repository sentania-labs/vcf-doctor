import assert from 'node:assert/strict'
import { test } from 'node:test'
import { consoleSession } from './consoleSession.ts'

test('providers mounted during an outage are not the ones that serve the recovered console', () => {
  const during = consoleSession({ database: 'unavailable' })
  const after = consoleSession({})
  assert.notEqual(during, after)
  assert.equal(consoleSession(null), after)
  // Staying live, or staying down across polls, keeps the same providers.
  assert.equal(consoleSession({}), consoleSession({}))
  assert.equal(consoleSession({ database: 'unavailable' }), during)
})
