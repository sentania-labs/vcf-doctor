import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { JSDOM } from 'jsdom'
import { Skeleton } from './Skeleton.ts'

test('loading placeholder exposes a labelled status', () => {
  const html = renderToStaticMarkup(createElement(Skeleton, { className: 'h-24' }))
  const dom = new JSDOM(html)
  const status = dom.window.document.querySelector('[role="status"]')
  assert.ok(status)
  assert.equal(status.getAttribute('aria-live'), 'polite')
  assert.equal(status.textContent?.trim(), 'Loading')
  assert.ok(status.classList.contains('h-24'))
  dom.window.close()
})
