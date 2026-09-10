import type { AssistantSettings, EventPolicyDefaultLimit } from '@/types'

// After a key rotation only the stored-key state can have changed on the
// server; everything else on the form is the operator's, saved or not.
export function withRefreshedKeyState(local: AssistantSettings, fresh: AssistantSettings): AssistantSettings {
  return { ...local, api_key_set: fresh.api_key_set, api_key_unreadable: fresh.api_key_unreadable }
}

export function eventPolicyDefaultLimitMessage(limit: EventPolicyDefaultLimit): string {
  const changes: string[] = []
  if (limit.configured.retention_hours !== limit.effective.retention_hours) {
    changes.push(`event retention from ${limit.configured.retention_hours.toLocaleString('en-US')} to ${limit.effective.retention_hours.toLocaleString('en-US')} hours`)
  }
  if (limit.configured.row_cap !== limit.effective.row_cap) {
    changes.push(`row cap from ${limit.configured.row_cap.toLocaleString('en-US')} to ${limit.effective.row_cap.toLocaleString('en-US')} per connection`)
  }
  return `The configured default was limited: ${changes.join('; ')}. These are the effective values until you save changes.`
}
