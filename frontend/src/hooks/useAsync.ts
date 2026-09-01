import { useCallback, useEffect, useRef, useState, type DependencyList } from 'react'

export interface AsyncState<T> {
  data: T | null
  error: Error | null
  loading: boolean
  reload: () => void
}

// Runs an async loader whenever deps change. Keeps the previous data visible during reloads.
export function useAsync<T>(loader: () => Promise<T>, deps: DependencyList, enabled = true): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(enabled)
  const [tick, setTick] = useState(0)
  const seq = useRef(0)

  useEffect(() => {
    if (!enabled) { setLoading(false); return }
    const mine = ++seq.current
    setLoading(true)
    loader().then(d => {
      if (seq.current !== mine) return
      setData(d); setError(null); setLoading(false)
    }).catch((e: unknown) => {
      if (seq.current !== mine) return
      setError(e instanceof Error ? e : new Error(String(e))); setLoading(false)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, enabled])

  const reload = useCallback(() => setTick(t => t + 1), [])
  return { data, error, loading, reload }
}

export function useInterval(fn: () => void, ms: number | null) {
  const ref = useRef(fn)
  ref.current = fn
  useEffect(() => {
    if (ms === null) return
    const id = setInterval(() => ref.current(), ms)
    return () => clearInterval(id)
  }, [ms])
}
