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
