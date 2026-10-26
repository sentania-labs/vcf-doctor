import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ssoMessage } from './ssoErrors.ts'

test('no code means no message', () => {
  assert.equal(ssoMessage(null), null)
  assert.equal(ssoMessage(''), null)
})

test('each reason the backend can give has its own wording', () => {
  const codes = ['unavailable', 'not_authorized', 'denied', 'expired', 'not_configured', 'failed']
  const messages = codes.map(c => ssoMessage(c))
  assert.ok(messages.every(m => typeof m === 'string' && m.length > 0))
  assert.equal(new Set(messages).size, codes.length)
})

test('an unknown or hostile code gets the generic failure, never its own text', () => {
  const generic = ssoMessage('failed')
  for (const code of ['nope', '<script>alert(1)</script>', 'constructor', '__proto__', 'toString']) {
    assert.equal(ssoMessage(code), generic)
  }
})
