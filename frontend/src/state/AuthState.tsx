import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
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

  // Any non-auth API call that comes back 401 drops us to the login page.
  useEffect(() => {
    const onUnauth = () => {
      setStatus(s => (s ? { ...s, authenticated: false } : s))
      if (location.pathname !== '/login') navigate('/login', { replace: true })
    }
    window.addEventListener(UNAUTHENTICATED_EVENT, onUnauth)
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, onUnauth)
  }, [navigate, location.pathname])

  const signOut = useCallback(async () => {
    try { await apiLogout() } catch { /* cookie may already be gone; go to login regardless */ }
    setStatus(s => (s ? { ...s, authenticated: false } : s))
    navigate('/login', { replace: true })
  }, [navigate])

  return <Ctx.Provider value={{ status, loading, error, refresh, signOut }}>{children}</Ctx.Provider>
}

export function useAuth(): AuthState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAuth outside provider')
  return v
}

// True when the user may see the console: auth disabled by deployment, or signed in.
export function isAuthed(s: AuthStatus | null): boolean {
  return !!s && (!s.enabled || s.authenticated)
}
