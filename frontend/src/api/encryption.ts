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

// Re-encrypt every stored secret the current key cannot open, using the previous
// key. A pasted key is sent once and never stored; passing useKeyFile instead
// rotates from the generated key file still on the volume, with no key in the
// browser. Credentials stay untouched if the key opens nothing.
export function rekeyEncryption(previousKey: string, useKeyFile = false): Promise<RekeyResult> {
  if (USE_MOCKS) {
    return delay({
      ok: true, message: 'Nothing to do: every stored secret already opens with the current key.',
      rewritten: 0, unreadable: 0, status: MOCK_STATUS,
    }, 400)
  }
  const body = useKeyFile ? { use_key_file: true } : { previous_key: previousKey }
  return apiSend<RekeyResult>('POST', '/settings/encryption/rekey', body)
}
