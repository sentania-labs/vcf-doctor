import { test } from 'node:test'
import { strict as assert } from 'node:assert'
import { encryptionErrorMessage } from './encryptionError.ts'

test('explains how to recover when the encryption key file cannot be created', () => {
  const message = encryptionErrorMessage('cannot create encryption key file /data/vcf-doctor.key: Permission denied. Point VCF_DOCTOR_DATA_DIR at a writable directory, or set VCF_DOCTOR_SECRET_KEY and no file is needed.')
  assert.match(message, /directory writable/)
  assert.match(message, /supply an encryption key/)
  assert.doesNotMatch(message, /VCF_DOCTOR_/)
})

test('explains recovery for an unreadable or corrupt encryption key file', () => {
  for (const cause of ['PermissionError', 'ValueError']) {
    const message = encryptionErrorMessage(`encryption key file /data/vcf-doctor.key is unreadable or corrupt (${cause}); restore it from backup, or remove it to generate a new key and re-enter credentials`)
    assert.match(message, /Restore it from backup/)
    assert.match(message, /re-enter stored credentials/)
  }
})

test('unknown key errors direct users to deployment help without exposing raw details', () => {
  const message = encryptionErrorMessage('unexpected VCF_DOCTOR_SECRET_KEY error')
  assert.match(message, /deployment details/)
  assert.doesNotMatch(message, /VCF_DOCTOR_/)
})
