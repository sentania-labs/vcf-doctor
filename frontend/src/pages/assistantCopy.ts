export function modelHint(source: 'live' | 'curated' | null, keySet: boolean): string {
  if (source === 'live') return 'List comes from your Anthropic account.'
  if (!keySet) return 'Add an API key to list the models available to your account.'
  if (source === null) return 'Loading models available to your account.'
  return 'This fallback list is shown because the provider could not be reached or rejected the key. Check the key and connection, then reload Settings to try again.'
}

export function apiKeyPlaceholder(keySet: boolean): string {
  return keySet ? 'Enter a key to save' : 'sk-ant-...'
}
