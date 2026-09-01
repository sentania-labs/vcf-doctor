import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { AlertTriangle, Stethoscope } from 'lucide-react'
import { login, setupPassword, USE_MOCKS } from '@/api'
import { isAuthed, useAuth } from '@/state/AuthState'
import { Button, Field, Input, Spinner } from '@/components/ui'

export default function LoginPage() {
  const { status, loading, error: statusError, refresh } = useAuth()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  if (status && isAuthed(status)) return <Navigate to="/" replace />
  const firstRun = !!status && !status.configured
  const unreachable = !status && !!statusError

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setErr(null)
    if (firstRun) {
      if (password.length < 8) { setErr('Password must be at least 8 characters.'); return }
      if (password !== confirm) { setErr('Passwords do not match.'); return }
    } else if (!password) { setErr('Enter the password.'); return }
    setBusy(true)
    try {
      if (firstRun) await setupPassword(password); else await login(password)
      await refresh()
      navigate('/', { replace: true })
    } catch (e2) {
      const msg = e2 instanceof Error ? e2.message : String(e2)
      setErr(msg === 'invalid password' ? 'Invalid password.' : msg)
      setBusy(false)
    }
  }

  return (
    <div className="h-full flex items-center justify-center bg-bg text-fg px-4">
      <div className="w-full max-w-[400px] anim-fade-up">
        <div className="rounded-xl border border-border bg-surface shadow-card">
          <div className="px-7 pt-7 pb-5 flex items-center gap-3 border-b border-border">
            <div className="h-10 w-10 rounded-lg bg-accent text-accent-fg flex items-center justify-center shrink-0"><Stethoscope size={20} /></div>
            <div className="leading-tight">
              <div className="text-[17px] font-semibold tracking-tight">VCF Doctor</div>
              <div className="text-xs text-faint">Operations console</div>
            </div>
          </div>

          <div className="px-7 py-6">
            {!status && loading ? (
              <div className="flex items-center justify-center py-6"><Spinner /></div>
            ) : unreachable ? (
              <div className="space-y-4">
                <div className="flex items-start gap-2.5 rounded-md bg-critical-bg px-3 py-2.5 text-sm">
                  <AlertTriangle size={16} className="text-critical mt-0.5 shrink-0" />
                  <div>
                    <div className="font-medium">Backend unreachable.</div>
                    <div className="text-muted mt-0.5">The VCF Doctor API did not answer. Check the container is running, then retry.</div>
                    {statusError ? <div className="font-mono text-xs text-muted mt-1.5">{statusError}</div> : null}
                  </div>
                </div>
                <Button className="w-full" onClick={() => void refresh()} loading={loading}>Retry</Button>
              </div>
            ) : (
              <form onSubmit={e => void submit(e)} className="space-y-4" noValidate>
                <div>
                  <h1 className="text-[15px] font-semibold tracking-tight">{firstRun ? 'Set the admin password' : 'Sign in'}</h1>
                  <p className="text-sm text-muted mt-0.5">
                    {firstRun ? 'This is the first run. Choose the password operators will use to open this console.' : 'Enter the operator password to continue.'}
                  </p>
                </div>
                <Field label={firstRun ? 'New password' : 'Password'} hint={firstRun ? 'At least 8 characters.' : undefined}>
                  <Input type="password" value={password} onChange={e => setPassword(e.target.value)} autoFocus autoComplete={firstRun ? 'new-password' : 'current-password'} disabled={busy} name="password" />
                </Field>
                {firstRun ? (
                  <Field label="Confirm password">
                    <Input type="password" value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password" disabled={busy} name="confirm" />
                  </Field>
                ) : null}
                {err ? <p className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2" role="alert">{err}</p> : null}
                <Button type="submit" variant="primary" className="w-full" loading={busy}>
                  {firstRun ? 'Set password and sign in' : 'Sign in'}
                </Button>
                {firstRun ? (
                  <p className="text-xs text-faint leading-relaxed">
                    This is a single shared operator password for everyone who uses this console. It can be changed later in Settings.
                  </p>
                ) : null}
              </form>
            )}
          </div>
        </div>
        <div className="mt-4 text-[11px] text-faint flex items-center justify-between px-1">
          <span>Read-only by design</span>
          {USE_MOCKS ? <span className="rounded bg-warning-bg text-warning px-1.5 py-0.5 font-semibold uppercase tracking-wider">mock data</span> : null}
        </div>
      </div>
    </div>
  )
}
