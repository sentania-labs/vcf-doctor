import { ChevronDown, ChevronRight } from 'lucide-react'
import type { ChangeLogEntry } from '@/types'
import { Badge } from '@/components/ui'
import { PropertyChanges, ResourceIcon, ResourceTypeLabel, significanceTone } from '@/components/domain'
import { formatDateTime, formatTime, relativeTime } from '@/lib/format'
import { cn } from '@/lib/cn'

// One persisted change-log row (GET /changes/log), collapsed to a single line and expandable
// to the property diff. Shared by the Changes timeline and the Environment page.
export function ChangeLogRow({ row, open, onToggle, onCompare }: { row: ChangeLogEntry; open: boolean; onToggle: () => void; onCompare?: (from: string, to: string) => void }) {
  const typeTone = row.change_type === 'added' ? 'ok' : row.change_type === 'removed' ? 'critical' : null
  const n = Object.keys(row.property_changes).length
  return (
    <div className={cn(open && 'bg-surface-2/50')}>
      <button onClick={onToggle} aria-expanded={open}
        className="w-full text-left px-4 py-2.5 grid grid-cols-[76px_auto_minmax(0,1fr)_16px] items-center gap-3 hover:bg-surface-2 transition-colors">
        <span className="text-[13px] text-muted tnum" title={formatDateTime(row.observed_at)}>{formatTime(row.observed_at)}</span>
        <span className="flex items-center gap-1.5">
          <Badge tone={significanceTone[row.significance]} dot>{row.significance}</Badge>
          {typeTone ? <Badge tone={typeTone}>{row.change_type}</Badge> : null}
        </span>
        <span className="min-w-0 flex items-baseline gap-2 flex-wrap">
          <span className="inline-flex items-center gap-1.5 text-sm font-semibold tracking-tight"><ResourceIcon type={row.resource_type} size={13} className="text-muted" />{row.resource_name}</span>
          <span className="text-xs text-faint"><ResourceTypeLabel type={row.resource_type} /></span>
          <span className="text-sm text-muted truncate">{row.summary}</span>
        </span>
        {open ? <ChevronDown size={15} className="text-faint" /> : <ChevronRight size={15} className="text-faint" />}
      </button>
      {open ? (
        <div className="px-4 pb-4 pl-[108px] space-y-3">
          <PropertyChanges change={row} />
          <div className="flex items-center gap-3 text-xs text-faint">
            <span>{n} {n === 1 ? 'property' : 'properties'} changed, observed {relativeTime(row.observed_at)}</span>
            {onCompare ? <button className="text-accent hover:underline ml-auto" onClick={() => onCompare(row.from_snapshot_id, row.to_snapshot_id)}>Compare these two snapshots</button> : null}
          </div>
        </div>
      ) : null}
    </div>
  )
}

// Count with a coloured dot, used in the header strip of the Changes and Environment pages.
export function SignificanceSummary({ n, label, dot }: { n: number; label: string; dot: string }) {
  return <span className="inline-flex items-center gap-2 text-sm"><span className={cn('h-2 w-2 rounded-full', dot)} /><span className="font-semibold tnum">{n}</span><span className="text-muted">{label}</span></span>
}
