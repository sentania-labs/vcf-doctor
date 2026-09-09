import type { AssistantSettings } from '@/types'

// After a key rotation only the stored-key state can have changed on the
// server; everything else on the form is the operator's, saved or not.
export function withRefreshedKeyState(local: AssistantSettings, fresh: AssistantSettings): AssistantSettings {
  return { ...local, api_key_set: fresh.api_key_set, api_key_unreadable: fresh.api_key_unreadable }
}
