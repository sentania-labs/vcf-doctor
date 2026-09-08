import { Info } from 'lucide-react'
import { getVersion } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { Card, CardHeader, ErrorState, Skeleton } from '@/components/ui'

function displayDate(value: string): string {
  if (value === 'unknown') return value
  const parsed = new Date(value)
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString()
}

export default function AboutCard() {
  const identity = useAsync(() => getVersion(), [])
  return (
    <Card>
      <CardHeader title={<span className="inline-flex items-center gap-2"><Info size={16} className="text-accent" /> About</span>}
        subtitle="Build identity for support, upgrades, and migration checks." />
      {identity.loading && !identity.data ? <div className="px-5 pb-5 grid sm:grid-cols-2 gap-4"><Skeleton className="h-12" /><Skeleton className="h-12" /></div> : null}
      {identity.error && !identity.data ? <ErrorState title="Build identity unavailable" error={identity.error} onRetry={identity.reload} /> : null}
      {identity.data ? (
        <dl className="px-5 pb-5 grid sm:grid-cols-2 gap-x-6 gap-y-4 text-sm">
          <div><dt className="text-xs font-medium text-muted mb-1">Version</dt><dd className="font-mono text-fg break-all">{identity.data.version}</dd></div>
          <div><dt className="text-xs font-medium text-muted mb-1">Built</dt><dd className="text-fg">{displayDate(identity.data.built_at)}</dd></div>
          <div><dt className="text-xs font-medium text-muted mb-1">Git commit</dt><dd className="font-mono text-fg break-all">{identity.data.sha}</dd></div>
          <div><dt className="text-xs font-medium text-muted mb-1">Python</dt><dd className="font-mono text-fg">{identity.data.python}</dd></div>
        </dl>
      ) : null}
    </Card>
  )
}
