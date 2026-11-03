import assert from 'node:assert/strict'
import { test } from 'node:test'
import { databaseHealthPresentation } from './databaseHealth.ts'

test('database health has two states and makes no claim without an answer', () => {
  assert.deepEqual(databaseHealthPresentation(true), {
    label: 'Healthy',
    tone: 'ok',
    message: 'The database connection is not editable here.',
  })
  assert.deepEqual(databaseHealthPresentation(false), {
    label: 'Not healthy',
    tone: 'critical',
    message: 'The console cannot use its database. Check that PostgreSQL is running and reachable from the console, and that its schema is up to date. This page updates when it is back.',
  })
  assert.equal(databaseHealthPresentation(null), null)
})

test('a database that is up but refuses the console, or is not migrated, says so', () => {
  const generic = databaseHealthPresentation(false)?.message
  assert.equal(databaseHealthPresentation(false, 'unreachable')?.message, generic)
  for (const reason of ['unmigrated', 'credentials', 'database']) {
    const message = databaseHealthPresentation(false, reason)?.message
    assert.notEqual(message, generic)
    assert.match(message ?? '', /This page updates when it is back\./)
  }
  assert.match(databaseHealthPresentation(false, 'credentials')?.message ?? '', /credentials/)
  assert.match(databaseHealthPresentation(false, 'database')?.message ?? '', /no database/)
  assert.match(databaseHealthPresentation(false, 'unmigrated')?.message ?? '', /migrations/)
  assert.equal(databaseHealthPresentation(true, 'credentials')?.label, 'Healthy')
})
