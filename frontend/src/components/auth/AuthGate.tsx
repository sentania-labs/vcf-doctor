import type { ReactNode } from 'react'
import { Fragment } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Stethoscope } from 'lucide-react'
import { Spinner } from '@/components/ui'
import { consoleSession } from './consoleSession'
import { databaseUnavailable, isAuthed, useAuth } from '@/state/AuthState'
import LoginPage from '@/pages/Login'

// Wraps the console. Shows a splash while status loads, the login page when
// the API is unreachable or the session is missing, and the children otherwise.
// While the database is unreachable nobody can be signed in or out, and every
// page but Settings would only fail, so the console opens on Settings, where
// the Database panel says what is wrong. That is safe to show unauthenticated:
// no session can be verified without the database, so no data call succeeds.
export function AuthGate({ children }: { children: ReactNode }) {
  const { status, loading, error } = useAuth()
  const { pathname } = useLocation()
  if (!status && loading) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-4 bg-bg text-fg">
        <div className="h-10 w-10 rounded-xl bg-accent text-accent-fg flex items-center justify-center"><Stethoscope size={20} /></div>
        <Spinner />
      </div>
    )
  }
  if (!status && error) return <LoginPage />
  if (databaseUnavailable(status)) return pathname === '/settings' ? <Fragment key={consoleSession(status)}>{children}</Fragment> : <Navigate to="/settings" replace />
  if (!isAuthed(status)) return <Navigate to="/login" replace />
  // Keyed so leaving an outage remounts the data providers and they load afresh.
  return <Fragment key={consoleSession(status)}>{children}</Fragment>
}
