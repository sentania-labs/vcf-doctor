import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { getAuthStatus, logout as apiLogout, type AuthStatus } from '@/api'
import { UNAUTHENTICATED_EVENT } from '@/api/client'

interface AuthState {
  status: AuthStatus | null      // null until the first status call resolves
  loading: boolean
  error: string | null           // status call failed (API unreachable)
  refresh: () => Promise<void>
  signOut: () => Promise<void>
}

const Ctx = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()
  const location = useLocation()

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setStatus(await getAuthStatus()); setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  // While the database is out the status is a stopgap, so keep asking; the
  // console carries on from wherever it is when the database comes back.
  const databaseDown = status?.database === 'unavailable'
  useEffect(() => {
    if (!databaseDown) return
    const id = setInterval(() => { void getAuthStatus().then(s => { setStatus(s); setError(null) }, () => { /* the next tick tries again */ }) }, 5000)
    return () => clearInterval(id)
  }, [databaseDown])

  // Any non-auth API call that comes back 401 drops us to the login page. Not while the
  // database is out: nobody can sign in then, and Settings is the one place left to go.
  const outage = useRef(false)
  outage.current = databaseDown
  useEffect(() => {
    const onUnauth = () => {
      if (outage.current) return
      setStatus(s => (s ? { ...s, authenticated: false } : s))
      if (location.pathname !== '/login') navigate('/login', { replace: true })
    }
    window.addEventListener(UNAUTHENTICATED_EVENT, onUnauth)
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, onUnauth)
  }, [navigate, location.pathname])

  const signOut = useCallback(async () => {
    let redirect: string | undefined
    try { redirect = (await apiLogout()).redirect } catch { /* cookie may already be gone; go to login regardless */ }
    setStatus(s => (s ? { ...s, authenticated: false, user: undefined } : s))
    // Someone who signed in through the identity provider is sent there to end its session too.
    if (redirect) { window.location.assign(redirect); return }
    navigate('/login', { replace: true })
  }, [navigate])

  return <Ctx.Provider value={{ status, loading, error, refresh, signOut }}>{children}</Ctx.Provider>
}

export function useAuth(): AuthState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAuth outside provider')
  return v
}

// True while the console is answering but cannot reach its database. Nobody can be signed in or out then.
export function databaseUnavailable(s: AuthStatus | null): boolean {
  return s?.database === 'unavailable'
}

// True when the user may see the console: auth disabled by deployment, or signed in.
export function isAuthed(s: AuthStatus | null): boolean {
  return !!s && (!s.enabled || s.authenticated)
}
