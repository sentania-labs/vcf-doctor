import { createElement } from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '../../lib/cn.ts'

export function Skeleton({ className }: { className?: string }) {
  return createElement('div', {
    role: 'status',
    'aria-live': 'polite',
    className: cn('rounded-md border border-border-strong bg-surface-2 min-h-8 flex items-center justify-center px-2 text-xs text-muted', className),
  }, createElement(Loader2, { size: 14, className: 'spin spin-icon mr-2', 'aria-hidden': true }), 'Loading')
}
