import type { EncryptionStatus } from '@/types'
import { apiGet } from './client'
import { USE_MOCKS, delay } from './mocks'

export function getEncryptionStatus(): Promise<EncryptionStatus> {
  if (USE_MOCKS) return delay({ enabled: true, key_source: 'file', key_env_var: 'VCF_DOCTOR_SECRET_KEY', key_file: '/data/vcf-doctor.key', unreadable_connections: [], assistant_key_unreadable: false }, 100)
  return apiGet<EncryptionStatus>('/settings/encryption')
}
