import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { Stethoscope } from 'lucide-react'
import { Spinner } from '@/components/ui'
import { isAuthed, useAuth } from '@/state/AuthState'
import LoginPage from '@/pages/Login'

// Wraps the console. Shows a splash while status loads, the login page when
// the API is unreachable or the session is missing, and the children otherwise.
export function AuthGate({ children }: { children: ReactNode }) {
  const { status, loading, error } = useAuth()
  if (!status && loading) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-4 bg-bg text-fg">
        <div className="h-10 w-10 rounded-xl bg-accent text-accent-fg flex items-center justify-center"><Stethoscope size={20} /></div>
        <Spinner />
      </div>
    )
  }
  if (!status && error) return <LoginPage />
  if (!isAuthed(status)) return <Navigate to="/login" replace />
  return <>{children}</>
}
