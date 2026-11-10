import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { AssistantSettings, Settings } from '../types/index.ts'
import {
  refreshedEventPolicy,
  refreshedFormState,
  refreshedMinSig,
  refreshedRetention,
  withRefreshedKeyState,
  type SettingsFormState,
} from './settingsForm.ts'

const saved: AssistantSettings = { enabled: true, provider: 'anthropic', model: 'claude-opus-5', api_key_set: true, api_key_stored: true, api_key_unreadable: true }

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

// What the server reports after the rotation. Every scalar here differs from
// the operator's unsaved edit below, so a full reload (s.reload(), which lets
// the useEffect on s.data overwrite the form) cannot pass these assertions.
const freshSettings: Settings = {
  retention_policy: { recent_days: 7, hourly_days: 21, daily_days: 90, timezone: 'Asia/Tokyo' },
  event_policy: { retention_hours: 12, row_cap: 5000 },
  changes_min_significance: 'high',
  assistant: { ...saved, api_key_unreadable: false },
}

// The page's local state, edited but not saved.
const editedForm: SettingsFormState = {
  retention: { recent_days: 3, hourly_days: 9, daily_days: 60, timezone: 'Europe/Berlin' },
  eventPolicy: { retention_hours: 24, row_cap: 100000 },
  minSig: 'medium',
  assistant: { ...saved, model: 'claude-sonnet-5', enabled: false },
}

test('a rotation refresh updates only the assistant key fields', () => {
  const next = refreshedFormState(editedForm, freshSettings)
  assert.equal(next.assistant.api_key_unreadable, false)
  assert.equal(next.assistant.api_key_set, true)
  // The other assistant fields are still the operator's unsaved choices.
  assert.equal(next.assistant.model, 'claude-sonnet-5')
  assert.equal(next.assistant.enabled, false)
})

test('a rotation refresh leaves the other form state untouched, even where the server differs', () => {
  const next = refreshedFormState(editedForm, freshSettings)
  // Same objects, not copies: only the assistant entry was rebuilt.
  assert.equal(next.retention, editedForm.retention)
  assert.equal(next.eventPolicy, editedForm.eventPolicy)
  assert.equal(next.minSig, editedForm.minSig)
  // Values are the unsaved edits, not the freshly fetched server values.
  assert.deepEqual(next.retention, { recent_days: 3, hourly_days: 9, daily_days: 60, timezone: 'Europe/Berlin' })
  assert.deepEqual(next.eventPolicy, { retention_hours: 24, row_cap: 100000 })
  assert.equal(next.minSig, 'medium')
  // Guards against a partial merge that copies the server's fields in.
  assert.notEqual(next.retention, freshSettings.retention_policy)
  assert.notEqual(next.eventPolicy, freshSettings.event_policy)
  assert.notEqual(next.minSig, freshSettings.changes_min_significance)
})

// The page applies the refresh inside functional setState calls, so each helper
// sees the value the field holds when it runs. These mirror that: the last write
// is the one the helper is handed.
test('each field keeps the value the functional update hands it, not an earlier snapshot', () => {
  // Operator edits after the rotation was requested; that later value is what
  // the updater receives, and it is what the refresh returns.
  const laterRetention = { ...editedForm.retention, recent_days: 4 }
  const laterEventPolicy = { ...editedForm.eventPolicy, row_cap: 200000 }
  assert.equal(refreshedRetention(laterRetention), laterRetention)
  assert.equal(refreshedEventPolicy(laterEventPolicy), laterEventPolicy)
  assert.equal(refreshedMinSig('high'), 'high')
  // The snapshot taken when the refresh started is not what survives.
  assert.notEqual(refreshedRetention(laterRetention), editedForm.retention)
  assert.notEqual(refreshedMinSig('high'), editedForm.minSig)
  // The whole-state helper composes the same per-field decisions.
  const composed = refreshedFormState({ ...editedForm, retention: laterRetention, eventPolicy: laterEventPolicy, minSig: 'high' }, freshSettings)
  assert.equal(composed.retention, laterRetention)
  assert.equal(composed.eventPolicy, laterEventPolicy)
  assert.equal(composed.minSig, 'high')
  assert.equal(composed.assistant.api_key_unreadable, false)
})
