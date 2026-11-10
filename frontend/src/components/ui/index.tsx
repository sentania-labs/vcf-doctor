import { useEffect, useRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react'
import { AlertTriangle, Inbox, Loader2, X } from 'lucide-react'
import { cn } from '@/lib/cn'
export { Skeleton } from './Skeleton'

/* ---------- Button ---------- */
type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'lg'
export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> { variant?: Variant; size?: Size; loading?: boolean }
const variantCls: Record<Variant, string> = {
  primary: 'lat-btn--primary', secondary: '', ghost: 'lat-btn--ghost', danger: 'lat-btn--danger',
}
const sizeCls: Record<Size, string> = { sm: 'h-8 px-3 text-[13px] gap-1.5', md: 'h-9 px-3.5 text-sm gap-2', lg: 'h-11 px-5 text-[15px] gap-2' }
export function Button({ variant = 'secondary', size = 'md', loading, className, children, disabled, ...rest }: ButtonProps) {
  return (
    <button
      className={cn('lat-btn inline-flex items-center justify-center select-none',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50 disabled:pointer-events-none',
        variantCls[variant], sizeCls[size], className)}
      disabled={disabled || loading} {...rest}
    >
      {loading ? <><Loader2 size={15} className="spin spin-icon" aria-hidden="true" /><span className="spin-label">Loading</span></> : null}
      {children}
    </button>
  )
}

/* ---------- Card ---------- */
export function Card({ className, children, onClick, interactive }: { className?: string; children: ReactNode; onClick?: () => void; interactive?: boolean }) {
  return (
    <div
      onClick={onClick}
      className={cn('lat-card p-0', interactive && 'cursor-pointer hover:border-border-strong hover:bg-surface-2', className)}
    >
      {children}
    </div>
  )
}
export function CardHeader({ title, subtitle, action, className }: { title: ReactNode; subtitle?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cn('flex items-start justify-between gap-4 px-5 pt-5 pb-3', className)}>
      <div>
        <h3 className="text-[15px] font-semibold tracking-tight">{title}</h3>
        {subtitle ? <p className="text-sm text-muted mt-0.5">{subtitle}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  )
}

/* ---------- Badge ---------- */
export type Tone = 'critical' | 'warning' | 'info' | 'ok' | 'neutral' | 'accent'
const toneCls: Record<Tone, string> = {
  critical: 'lat-badge--bad', warning: 'lat-badge--warn', info: 'lat-badge--accent',
  ok: 'lat-badge--ok', neutral: '', accent: 'lat-badge--accent',
}
export function Badge({ tone = 'neutral', children, className, dot }: { tone?: Tone; children: ReactNode; className?: string; dot?: boolean }) {
  return (
    <span className={cn('lat-badge inline-flex items-center gap-1.5 whitespace-nowrap', toneCls[tone], className)}>
      {dot ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : null}
      {children}
    </span>
  )
}

/* ---------- Inputs ---------- */
export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn('lat-input h-9 placeholder:text-faint',
        'focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/25 disabled:opacity-50', className)}
      {...rest}
    />
  )
}
export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn('lat-select h-9 pr-8 appearance-none',
        'bg-[url("data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2212%22%20height%3D%2212%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%23888%22%20stroke-width%3D%222.5%22%3E%3Cpath%20d%3D%22m6%209%206%206%206-6%22%2F%3E%3C%2Fsvg%3E")] bg-no-repeat bg-[right_0.6rem_center]',
        'focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/25 disabled:opacity-50', className)}
      {...rest}
    >
      {children}
    </select>
  )
}
export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="lat-label">{label}</span>
      {children}
      {hint ? <span className="lat-hint block mt-1">{hint}</span> : null}
    </label>
  )
}
export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label?: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)}
      className={cn('inline-flex items-center gap-2.5 disabled:opacity-50', label && 'text-sm')}>
      <span className={cn('relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border',
        checked ? 'bg-accent border-accent' : 'bg-surface-2 border-border-strong')}>
        <span className={cn('absolute h-3.5 w-3.5 rounded-full', checked ? 'translate-x-[18px] bg-accent-fg' : 'translate-x-[3px] bg-border-strong')} />
      </span>
      {label ? <span>{label}</span> : null}
    </button>
  )
}

/* ---------- Segmented control ---------- */
export function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: Array<{ value: T; label: ReactNode }> }) {
  return (
    <div className="inline-flex max-w-full flex-wrap rounded-lg border border-border bg-surface p-0.5">
      {options.map(o => (
        <button key={o.value} onClick={() => onChange(o.value)}
          className={cn('h-8 whitespace-nowrap rounded-md border px-3 text-[13px] font-medium inline-flex items-center gap-1.5',
            value === o.value ? 'border-accent bg-accent-soft text-accent-ink' : 'border-transparent text-muted hover:text-fg')}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/* ---------- Escape handling: only the topmost open layer (dialog or drawer) closes ---------- */
const layerStack: symbol[] = []
function useEscapeToClose(open: boolean, onClose: () => void) {
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    if (!open) return
    const id = Symbol('layer')
    layerStack.push(id)
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && layerStack[layerStack.length - 1] === id) closeRef.current() }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      const i = layerStack.indexOf(id)
      if (i >= 0) layerStack.splice(i, 1)
    }
  }, [open])
}

/* ---------- Dialog ---------- */
export function Dialog({ open, onClose, title, children, footer, width = 'max-w-lg' }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode; width?: string }) {
  useEscapeToClose(open, onClose)
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className={cn('relative w-full rounded-xl border border-border bg-surface', width)}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h2 className="text-base font-semibold tracking-tight">{title}</h2>
          <button onClick={onClose} className="text-muted hover:text-fg rounded-md p-1" aria-label="Close"><X size={16} /></button>
        </div>
        <div className="px-5 py-4">{children}</div>
        {footer ? <div className="flex justify-end gap-2 px-5 py-4 border-t border-border">{footer}</div> : null}
      </div>
    </div>
  )
}

/* ---------- Drawer (right side) ---------- */
export function Drawer({ open, onClose, title, children, width = 'w-[520px]', header }: { open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode; width?: string; header?: ReactNode }) {
  useEscapeToClose(open, onClose)
  if (!open) return null
  return (
    <div className="fixed inset-0 z-40">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <aside className={cn('absolute right-0 top-0 h-full max-w-full bg-surface border-l border-border flex flex-col', width)}>
        <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-border shrink-0">
          {header ?? <h2 className="text-base font-semibold tracking-tight truncate">{title}</h2>}
          <button onClick={onClose} className="text-muted hover:text-fg rounded-md p-1 shrink-0" aria-label="Close"><X size={16} /></button>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto">{children}</div>
      </aside>
    </div>
  )
}

/* ---------- States ---------- */
export function Spinner({ className, label = 'Loading' }: { className?: string; label?: string }) {
  return (
    <span role="status" className={cn('inline-flex items-center gap-2 text-muted', className)}>
      <Loader2 size={18} className="spin spin-icon" aria-hidden="true" />
      <span>{label}</span>
    </span>
  )
}
export function EmptyState({ icon, title, body, action }: { icon?: ReactNode; title: string; body?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-14 px-6">
      <div className="h-11 w-11 rounded-full bg-surface-2 border border-border flex items-center justify-center text-muted mb-4">{icon ?? <Inbox size={20} />}</div>
      <h3 className="text-[15px] font-semibold">{title}</h3>
      {body ? <p className="text-sm text-muted mt-1.5 max-w-md leading-relaxed">{body}</p> : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  )
}
export function ErrorState({ title = 'Could not load', error, onRetry }: { title?: string; error: Error | string | null; onRetry?: () => void }) {
  const msg = typeof error === 'string' ? error : error?.message
  return (
    <div className="lat-banner lat-banner--bad m-5 flex flex-col items-center justify-center text-center py-12 px-6" role="alert">
      <div className="mb-4"><AlertTriangle size={20} /></div>
      <h3 className="text-[15px] font-semibold">{title}</h3>
      {msg ? <p className="text-sm mt-1.5">{msg}</p> : null}
      {onRetry ? <Button className="mt-5" size="sm" onClick={onRetry}>Retry</Button> : null}
    </div>
  )
}
export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 mb-7">
      <div>
        <h1 className="page-title">{title}</h1>
        {subtitle ? <p className="text-sm text-muted mt-1">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  )
}
