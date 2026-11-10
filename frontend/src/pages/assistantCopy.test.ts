import { test } from 'node:test'
import { strict as assert } from 'node:assert'
import { apiKeyPlaceholder, modelHint } from './assistantCopy.ts'

test('model guidance distinguishes missing keys, loading, live and fallback lists', () => {
  assert.match(modelHint('curated', false), /Add an API key/)
  assert.match(modelHint(null, true), /Loading/)
  assert.match(modelHint('live', true), /your Anthropic account/)
  assert.match(modelHint('curated', true), /fallback list.*Check the key and connection.*reload Settings/)
})

test('a configured key can be saved without claiming a stored key exists', () => {
  assert.equal(apiKeyPlaceholder(true), 'Enter a key to save')
  assert.equal(apiKeyPlaceholder(false), 'sk-ant-...')
})
