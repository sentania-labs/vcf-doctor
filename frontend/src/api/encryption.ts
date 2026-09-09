import type { EncryptionStatus, RekeyResult } from '@/types'
import { apiGet, apiSend } from './client'
import { USE_MOCKS, delay } from './mocks'

const MOCK_STATUS: EncryptionStatus = {
  enabled: true, key_source: 'file', key_env_var: 'VCF_DOCTOR_SECRET_KEY',
  key_previous_env_var: 'VCF_DOCTOR_SECRET_KEY_PREVIOUS', key_file: '/data/vcf-doctor.key',
  previous_key_file: null, unreadable_connections: [], assistant_key_unreadable: false, last_rekey: null,
}

export function getEncryptionStatus(): Promise<EncryptionStatus> {
  if (USE_MOCKS) return delay(MOCK_STATUS, 100)
  return apiGet<EncryptionStatus>('/settings/encryption')
}

// Re-encrypt every stored secret the current key cannot open, using the
// generated key file still on the volume. No key material passes through the
// browser, and credentials stay untouched if that key opens nothing.
export function rekeyEncryption(): Promise<RekeyResult> {
  if (USE_MOCKS) {
    return delay({
      ok: true, message: 'Nothing to do: every stored secret already opens with the current key.',
      rewritten: 0, unreadable: 0, status: MOCK_STATUS,
    }, 400)
  }
  return apiSend<RekeyResult>('POST', '/settings/encryption/rekey')
}
