import { NavLink, Outlet } from 'react-router-dom'
import { Activity, Bot, Boxes, Camera, GitCompareArrows, HeartPulse, LayoutDashboard, Plug, Settings as SettingsIcon, Stethoscope } from 'lucide-react'
import { TopBar } from './TopBar'
import { AssistantDrawer } from '@/components/assistant/AssistantDrawer'
import { useAppState } from '@/state/AppState'
import { cn } from '@/lib/cn'
import { USE_MOCKS } from '@/api'

const primary = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/health', label: 'Health', icon: HeartPulse },
  { to: '/changes', label: 'Changes', icon: GitCompareArrows },
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
      className={({ isActive }) => cn('flex items-center gap-3 rounded-md px-3 h-9 text-[13.5px] font-medium transition-colors duration-150',
        isActive ? 'bg-surface-3 text-fg' : 'text-muted hover:text-fg hover:bg-surface-2')}>
      {({ isActive }) => (<>
        <Icon size={16} className={isActive ? 'text-accent' : 'text-faint'} />
        {label}
      </>)}
    </NavLink>
  )
}

export function Shell() {
  const { backend, backendError } = useAppState()
  return (
    <div className="h-full flex bg-bg text-fg">
      <aside className="w-[232px] shrink-0 border-r border-border bg-surface flex flex-col">
        <div className="h-16 flex items-center gap-2.5 px-5 border-b border-border">
          <div className="h-8 w-8 rounded-lg bg-accent text-accent-fg flex items-center justify-center"><Stethoscope size={17} /></div>
          <div className="leading-tight">
            <div className="text-[15px] font-semibold tracking-tight">VCF Doctor</div>
            <div className="text-[11px] text-faint">Operations console</div>
          </div>
        </div>
        <nav className="flex-1 px-3 py-4 flex flex-col gap-0.5">
          {primary.map(n => <NavItem key={n.to} {...n} />)}
          <div className="my-4 border-t border-border" />
          {secondary.map(n => <NavItem key={n.to} {...n} />)}
        </nav>
        <div className="px-5 py-4 border-t border-border text-[11px] text-faint flex items-center justify-between">
          <span>Read-only by design</span>
          {USE_MOCKS ? <span className="rounded bg-warning-bg text-warning px-1.5 py-0.5 font-semibold uppercase tracking-wider">mock data</span> : null}
        </div>
      </aside>
      <div className="flex-1 min-w-0 flex flex-col">
        <TopBar />
        {backend === 'down' ? (
          <div className="bg-critical-bg border-b border-critical/40 px-6 py-2.5 text-sm flex items-center gap-2.5 text-fg">
            <span className="h-2 w-2 rounded-full bg-critical anim-pulse" />
            <span className="font-medium">Backend unreachable.</span>
            <span className="text-muted">Pages show the last data they loaded. Retrying every 5 seconds.</span>
            {backendError ? <span className="ml-auto font-mono text-xs text-muted">{backendError}</span> : null}
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
