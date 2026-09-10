import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { AssistantSettings } from '../types/index.ts'
import { eventPolicyDefaultLimitMessage, withRefreshedKeyState } from './settingsForm.ts'

const saved: AssistantSettings = { enabled: true, provider: 'anthropic', model: 'claude-opus-5', api_key_set: true, api_key_unreadable: true }

test('a rotation refreshes the stored-key state and keeps unsaved edits', () => {
  // Operator changes the model and disables the assistant, but has not saved.
  const edited: AssistantSettings = { ...saved, model: 'claude-sonnet-5', enabled: false }
  // The rotation re-encrypted the stored key: the server now reads it fine.
  const fresh: AssistantSettings = { ...saved, api_key_unreadable: false }
  const after = withRefreshedKeyState(edited, fresh)
  assert.equal(after.api_key_unreadable, false)
  assert.equal(after.api_key_set, true)
  assert.equal(after.model, 'claude-sonnet-5')
  assert.equal(after.enabled, false)
  assert.equal(after.provider, 'anthropic')
})

test('a rotation that recovered nothing leaves the badge as it was', () => {
  const after = withRefreshedKeyState({ ...saved, model: 'x' }, saved)
  assert.deepEqual(after, { ...saved, model: 'x' })
})

test('a limited event default names configured and effective values', () => {
  assert.equal(
    eventPolicyDefaultLimitMessage({
      configured: { retention_hours: 100000, row_cap: 500 },
      effective: { retention_hours: 8760, row_cap: 1000 },
    }),
    'The configured default was limited: event retention from 100,000 to 8,760 hours; row cap from 500 to 1,000 per connection. These are the effective values until you save changes.',
  )
})
