import type { AssistantSettings, EventPolicy, RetentionPolicy, Settings, Significance } from '@/types'

// Everything the Settings page holds as editable form state. Named so the
// rotation refresh can carry the untouched fields along by reference.
export interface SettingsFormState {
  retention: RetentionPolicy
  eventPolicy: EventPolicy
  minSig: Significance
  assistant: AssistantSettings
}

// After a key rotation only the stored-key state can have changed on the
// server; everything else on the form is the operator's, saved or not.
export function withRefreshedKeyState(local: AssistantSettings, fresh: AssistantSettings): AssistantSettings {
  return { ...local, api_key_set: fresh.api_key_set, api_key_unreadable: fresh.api_key_unreadable }
}

// The form state after a rotation: the assistant's key fields follow the server,
// every other field is the exact object the operator was editing. A full reload
// (the whole settings object back into local state) would overwrite those edits
// with what is stored, which is the defect this replaces.
export function refreshedFormState(local: SettingsFormState, fresh: Settings): SettingsFormState {
  return {
    retention: local.retention,
    eventPolicy: local.eventPolicy,
    minSig: local.minSig,
    assistant: withRefreshedKeyState(local.assistant, fresh.assistant),
  }
}
