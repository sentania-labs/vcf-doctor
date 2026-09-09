import assert from 'node:assert/strict'
import { test } from 'node:test'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { JSDOM } from 'jsdom'
import { useAsync, type AsyncState } from './useAsync.ts'

test('mutation status survives an older pending refresh', async t => {
  const dom = new JSDOM('<div id="root"></div>')
  const globals = {
    window: dom.window,
    document: dom.window.document,
    IS_REACT_ACT_ENVIRONMENT: true,
  }
  const previous = Object.getOwnPropertyDescriptors(globalThis)
  for (const [key, value] of Object.entries(globals)) {
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value })
  }
  t.after(() => {
    dom.window.close()
    for (const key of Object.keys(globals)) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key])
      else Reflect.deleteProperty(globalThis, key)
    }
  })

  for (const outcome of ['success', 'failure'] as const) {
    await t.test(`ignores stale ${outcome} and still allows later reloads`, async t => {
      const container = dom.window.document.getElementById('root')!
      const root = createRoot(container)
      t.after(async () => { await act(async () => root.unmount()) })
      const pending: { resolve: (value: string) => void; reject: (error: Error) => void }[] = []
      const loader = () => new Promise<string>((resolve, reject) => pending.push({ resolve, reject }))
      let state!: AsyncState<string>
      function Status() {
        state = useAsync(loader, [])
        return createElement('output', null, JSON.stringify({
          data: state.data, error: state.error?.message ?? null, loading: state.loading,
        }))
      }

      await act(async () => root.render(createElement(Status)))
      await act(async () => pending[0].resolve('Needs re-entry'))
      await act(async () => state.reload())
      assert.equal(state.loading, true)
      assert.equal(pending.length, 2)

      await act(async () => state.set('Encrypted'))
      const recovered = JSON.stringify({ data: 'Encrypted', error: null, loading: false })
      assert.equal(container.textContent, recovered)

      await act(async () => {
        if (outcome === 'success') pending[1].resolve('Needs re-entry')
        else pending[1].reject(new Error('Old request failed'))
      })
      assert.equal(container.textContent, recovered)

      await act(async () => state.reload())
      assert.equal(state.loading, true)
      assert.equal(pending.length, 3)
      await act(async () => pending[2].resolve('Current server status'))
      assert.equal(container.textContent, JSON.stringify({
        data: 'Current server status', error: null, loading: false,
      }))
    })
  }
})
