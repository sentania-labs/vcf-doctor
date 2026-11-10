import { NavLink, Outlet } from 'react-router-dom'
import { useState } from 'react'
import { Activity, Bot, Boxes, Camera, GitCompareArrows, Globe, HeartPulse, LayoutDashboard, LogOut, Plug, ScrollText, Settings as SettingsIcon, Stethoscope } from 'lucide-react'
import { TopBar } from './TopBar'
import { AssistantDrawer } from '@/components/assistant/AssistantDrawer'
import { useAppState } from '@/state/AppState'
import { databaseUnavailable, useAuth } from '@/state/AuthState'
import { databaseOutage } from '@/state/databaseOutage'
import { cn } from '@/lib/cn'
import { USE_MOCKS } from '@/api'

const primary = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/health', label: 'Health', icon: HeartPulse },
  { to: '/changes', label: 'Changes', icon: GitCompareArrows },
  { to: '/environment', label: 'Environment', icon: Globe },
  { to: '/events', label: 'Events', icon: ScrollText },
  { to: '/inventory', label: 'Inventory', icon: Boxes },
  { to: '/snapshots', label: 'Snapshots', icon: Camera },
  { to: '/assistant', label: 'Assistant', icon: Bot },
]
const secondary = [
  { to: '/connections', label: 'Connections', icon: Plug },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
]

function NavItem({ to, label, icon: Icon, end }: { to: string; label: string; icon: typeof Activity; end?: boolean }) {
  return (
    <NavLink to={to} end={end}
      className={({ isActive }) => cn('flex items-center gap-3 rounded-md px-3 h-9 text-[13.5px] font-medium',
        isActive ? 'bg-accent-soft text-fg' : 'text-muted hover:text-fg hover:bg-surface-2')}>
      {({ isActive }) => (<>
        <Icon size={16} className={isActive ? 'text-accent-ink' : 'text-muted'} />
        {label}
      </>)}
    </NavLink>
  )
}

function SignOutItem() {
  const { status, signOut } = useAuth()
  const [busy, setBusy] = useState(false)
  if (!status?.enabled) return null
  return (
    <>
    {status.user ? (
      <div className="px-3 pb-2 text-xs text-muted leading-snug" title={`${status.user.subject} at ${status.user.issuer}`}>
        Signed in as <span className="font-medium text-fg break-words">{status.user.name}</span>
      </div>
    ) : null}
    <button type="button" disabled={busy} onClick={() => { setBusy(true); void signOut().finally(() => setBusy(false)) }}
      className={cn('flex items-center gap-3 rounded-md px-3 h-9 text-[13.5px] font-medium w-full text-left',
        'text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-50')}>
      <LogOut size={16} className="text-faint" />
      Sign out
    </button>
    </>
  )
}

export function Shell() {
  const { backend, backendError, databaseHealthy } = useAppState()
  const { status } = useAuth()
  // With the database out, only Settings (where its status lives) can do anything.
  const outage = databaseUnavailable(status)
  const databaseDown = databaseOutage(status, databaseHealthy, backend)
  return (
    <div className="h-full flex bg-bg text-fg">
      <aside className="w-[232px] shrink-0 border-r border-border bg-surface flex flex-col">
        <div className="h-16 flex items-center gap-2.5 px-5 border-b border-border">
          <div className="h-8 w-8 rounded-lg bg-accent text-accent-fg flex items-center justify-center"><Stethoscope size={17} /></div>
          <div className="leading-tight">
            <div className="text-[15px] font-semibold tracking-tight">VCF Doctor</div>
            <div className="text-[11px] text-muted">Operations console</div>
          </div>
        </div>
        <nav className="console-nav flex-1 px-3 py-4 flex flex-col gap-0.5">
          {outage ? null : <>
            {primary.map(n => <NavItem key={n.to} {...n} />)}
            <div className="my-4 border-t border-border" />
          </>}
          {secondary.filter(n => !outage || n.to === '/settings').map(n => <NavItem key={n.to} {...n} />)}
          <div className="mt-auto pt-4">{outage ? null : <SignOutItem />}</div>
        </nav>
        <div className="px-5 py-4 border-t border-border text-[11px] text-muted flex items-center justify-between">
          <span>Read-only by design</span>
          {USE_MOCKS ? <span className="rounded bg-warning-bg text-warning px-1.5 py-0.5 font-semibold uppercase tracking-wider">mock data</span> : null}
        </div>
      </aside>
      <div className="flex-1 min-w-0 flex flex-col">
        <TopBar />
        {backend === 'down' ? (
          <div className="lat-banner lat-banner--bad rounded-none flex items-start gap-2.5" role="alert">
            <span className="h-2 w-2 mt-1.5 shrink-0 rounded-full bg-current" />
            <div className="flex-1 min-w-0">
            {databaseDown ? (<>
              <span className="font-medium">The database is unreachable.</span>
              {' '}<span>The console is running, but it cannot read or save anything until PostgreSQL is back. Retrying every 5 seconds.</span>
            </>) : (<>
              <span className="font-medium">This console cannot serve.</span>
              {' '}<span>Its backend is unreachable. Pages show the last data they loaded. Retrying every 5 seconds.</span>
            </>)}
            {backendError && !databaseDown ? <span className="ml-2 font-mono text-xs">{backendError}</span> : null}
            </div>
          </div>
        ) : null}
        <main className="flex-1 min-h-0 overflow-y-auto">
          <div className="max-w-[1480px] mx-auto px-8 py-8">
            <Outlet />
          </div>
        </main>
      </div>
      <AssistantDrawer />
    </div>
  )
}
