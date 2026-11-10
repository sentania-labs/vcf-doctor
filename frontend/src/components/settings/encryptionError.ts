// The server's key errors contain deployment details; keep those in the disclosure.
export function encryptionErrorMessage(error: string): string {
  if (error.startsWith('cannot create encryption key file ')) {
    return 'The encryption key file could not be created. Make the key storage directory writable or supply an encryption key in the deployment.'
  }
  if (error.startsWith('encryption key file ') && error.includes(' is unreadable or corrupt (')) {
    return 'The encryption key file is unreadable or corrupt. Restore it from backup, or remove it to generate a new key and re-enter stored credentials.'
  }
  return 'The encryption key is unavailable. Check the deployment details and contact the person who manages the deployment.'
}

export function keyFileDescription(source: 'env' | 'file', error?: string | null): string | null {
  if (source !== 'file' || error) return null
  return 'The key file lives on the persistent volume with owner-only permissions. A key supplied by the deployment takes precedence over it. The key is never shown here.'
}

export function plainRotation(text: string, names: { key_env_var: string; key_previous_env_var: string }): string {
  if (text.startsWith('cannot create encryption key file ')) return encryptionErrorMessage(text)
  return text.replaceAll(names.key_previous_env_var, 'the previous deployment key').replaceAll(names.key_env_var, 'the deployment key')
}
