import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Search } from 'lucide-react'
import type { Resource } from '@/types'
import { getResources } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { useAppState } from '@/state/AppState'
import { Badge, Card, EmptyState, ErrorState, Input, PageHeader, Skeleton } from '@/components/ui'
import { ResourceIcon, ResourceTypeLabel } from '@/components/domain'
import { PropertyGroups } from '@/components/properties'
import { humanKey } from '@/lib/format'
import { cn } from '@/lib/cn'

const TREE_TYPES = new Set(['vcenter', 'datacenter', 'cluster', 'host', 'vm'])

export default function InventoryPage() {
  const { connectionId, refreshKey } = useAppState()
  const res = useAsync(() => getResources(connectionId), [connectionId, refreshKey])
  const [selected, setSelected] = useState<Resource | null>(null)
  const [q, setQ] = useState('')

  const { roots, childrenOf, extras, byId } = useMemo(() => {
    const all = res.data ?? []
    const byId = new Map(all.map(r => [r.id, r]))
    const childrenOf = new Map<string, Resource[]>()
    const roots: Resource[] = []
    const extras: Record<string, Resource[]> = { datastore: [], network: [] }
    for (const r of all) {
      if (!TREE_TYPES.has(r.type)) { (extras[r.type] ??= []).push(r); continue }
      if (r.parent_id && byId.has(r.parent_id)) {
        const list = childrenOf.get(r.parent_id) ?? []
        list.push(r); childrenOf.set(r.parent_id, list)
      } else roots.push(r)
    }
    const order: Record<string, number> = { vcenter: 0, datacenter: 1, cluster: 2, host: 3, vm: 4 }
    const sortFn = (a: Resource, b: Resource) => (order[a.type] ?? 9) - (order[b.type] ?? 9) || a.name.localeCompare(b.name)
    roots.sort(sortFn)
    for (const l of childrenOf.values()) l.sort(sortFn)
    return { roots, childrenOf, extras, byId }
  }, [res.data])

  const query = q.trim().toLowerCase()
  const matches = (r: Resource) => !query || r.name.toLowerCase().includes(query) || r.type.includes(query)
  const subtreeMatches = (r: Resource): boolean => matches(r) || (childrenOf.get(r.id) ?? []).some(subtreeMatches)

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const r of res.data ?? []) c[r.type] = (c[r.type] ?? 0) + 1
    return c
  }, [res.data])

  return (
    <div className="anim-fade-up">
      <PageHeader title="Inventory" subtitle={res.data ? Object.entries(counts).map(([k, v]) => `${v} ${k}${v === 1 ? '' : 's'}`).join(', ') : 'Everything the collector sees, arranged by containment'}
        actions={<div className="relative"><Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" /><Input value={q} onChange={e => setQ(e.target.value)} placeholder="Filter by name" className="pl-8 w-64" /></div>} />

      {res.error && !res.data ? <Card><ErrorState title="Inventory unavailable" error={res.error} onRetry={res.reload} /></Card>
        : res.loading && !res.data ? <div className="grid gap-5 xl:grid-cols-[1fr_400px]"><Skeleton className="h-[480px] rounded-xl" /><Skeleton className="h-[480px] rounded-xl" /></div>
        : (res.data?.length ?? 0) === 0 ? <Card><EmptyState title="No inventory yet" body="Run a scan to collect the environment. Resources appear here grouped vCenter, datacenter, cluster, host, VM." /></Card>
        : (
          <div className="grid gap-5 xl:grid-cols-[1fr_420px] items-start">
            <div className="space-y-5">
              <Card className="py-2">
                {roots.filter(subtreeMatches).map(r => <TreeNode key={r.id} r={r} depth={0} childrenOf={childrenOf} selected={selected} onSelect={setSelected} filter={query ? subtreeMatches : undefined} />)}
              </Card>
              <div className="grid gap-5 md:grid-cols-2">
                {(['datastore', 'network'] as const).map(t => (
                  <Card key={t} className="py-2">
                    <div className="px-4 py-2 text-xs font-semibold uppercase tracking-wider text-faint flex items-center gap-2"><ResourceIcon type={t} size={13} />{t === 'datastore' ? 'Datastores' : 'Networks'} <span className="tnum">{(extras[t] ?? []).length}</span></div>
                    {(extras[t] ?? []).filter(matches).map(r => <LeafRow key={r.id} r={r} selected={selected} onSelect={setSelected} />)}
                    {(extras[t] ?? []).filter(matches).length === 0 ? <p className="px-4 py-3 text-sm text-faint">None</p> : null}
                  </Card>
                ))}
              </div>
            </div>
            <Card className="xl:sticky xl:top-0 xl:max-h-[calc(100vh-2rem)] xl:overflow-y-auto">
              {selected ? <Properties r={selected} byId={byId} onSelect={setSelected} /> : <EmptyState title="Select a resource" body="Click any item in the tree to see its collected properties and relationships." />}
            </Card>
          </div>
        )}
    </div>
  )
}

function statusDot(r: Resource): string | null {
  const p = r.properties
  if (r.type === 'host') return p.connectionState === 'connected' ? 'bg-ok' : 'bg-critical'
  if (r.type === 'vm') return p.powerState === 'poweredOn' ? 'bg-ok' : 'bg-faint'
  if (r.type === 'datastore') { const u = Number(p.usedPct); return Number.isNaN(u) ? null : u >= 85 ? 'bg-warning' : 'bg-ok' }
  return null
}

function TreeNode({ r, depth, childrenOf, selected, onSelect, filter }: { r: Resource; depth: number; childrenOf: Map<string, Resource[]>; selected: Resource | null; onSelect: (r: Resource) => void; filter?: (r: Resource) => boolean }) {
  const kids = (childrenOf.get(r.id) ?? []).filter(k => !filter || filter(k))
  const [open, setOpen] = useState(depth < 3 || !!filter)
  const expanded = filter ? true : open
  const dot = statusDot(r)
  return (
    <div>
      <div className={cn('flex items-center gap-1.5 h-9 pr-3 cursor-pointer rounded-md mx-2 transition-colors', selected?.id === r.id ? 'bg-surface-3' : 'hover:bg-surface-2')} style={{ paddingLeft: 8 + depth * 20 }} onClick={() => onSelect(r)}>
        <button onClick={e => { e.stopPropagation(); setOpen(o => !o) }} className={cn('h-5 w-5 flex items-center justify-center text-faint hover:text-fg rounded', kids.length === 0 && 'invisible')}>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <ResourceIcon type={r.type} size={14} className="text-muted shrink-0" />
        <span className="text-sm truncate">{r.name}</span>
        {dot ? <span className={cn('ml-1.5 h-1.5 w-1.5 rounded-full shrink-0', dot)} /> : null}
        <span className="ml-auto text-[11px] text-faint uppercase tracking-wider shrink-0"><ResourceTypeLabel type={r.type} />{kids.length ? ` ${kids.length}` : ''}</span>
      </div>
      {expanded && kids.map(k => <TreeNode key={k.id} r={k} depth={depth + 1} childrenOf={childrenOf} selected={selected} onSelect={onSelect} filter={filter} />)}
    </div>
  )
}

function LeafRow({ r, selected, onSelect }: { r: Resource; selected: Resource | null; onSelect: (r: Resource) => void }) {
  const dot = statusDot(r)
  const used = r.type === 'datastore' ? Number(r.properties.usedPct) : NaN
  return (
    <div className={cn('flex items-center gap-2 h-9 px-3 mx-2 rounded-md cursor-pointer transition-colors', selected?.id === r.id ? 'bg-surface-3' : 'hover:bg-surface-2')} onClick={() => onSelect(r)}>
      <ResourceIcon type={r.type} size={14} className="text-muted shrink-0" />
      <span className="text-sm truncate">{r.name}</span>
      {dot ? <span className={cn('h-1.5 w-1.5 rounded-full shrink-0', dot)} /> : null}
      {!Number.isNaN(used) ? <span className={cn('ml-auto text-xs tnum', used >= 85 ? 'text-warning font-semibold' : 'text-muted')}>{used}% used</span>
        : r.properties.kind ? <span className="ml-auto text-[11px] text-faint">{String(r.properties.kind).replace('DistributedVirtualPortgroup', 'VDS portgroup').replace('NsxSegment', 'NSX segment')}</span> : null}
    </div>
  )
}

function Properties({ r, byId, onSelect }: { r: Resource; byId: Map<string, Resource>; onSelect: (r: Resource) => void }) {
  const parent = r.parent_id ? byId.get(r.parent_id) : null
  return (
    <div>
      <div className="px-5 pt-5 pb-4 border-b border-border">
        <div className="flex items-center gap-2 mb-1.5"><Badge tone="neutral"><ResourceTypeLabel type={r.type} /></Badge>{statusDot(r) ? <span className={cn('h-2 w-2 rounded-full', statusDot(r))} /> : null}</div>
        <h2 className="text-lg font-semibold tracking-tight break-all">{r.name}</h2>
        <p className="text-xs text-faint font-mono mt-1 break-all">{r.id}</p>
        {parent ? <p className="text-sm text-muted mt-2">in <button onClick={() => onSelect(parent)} className="text-accent hover:underline">{parent.name}</button></p> : null}
      </div>
      <div className="px-5 py-4">
        <PropertyGroups properties={r.properties} />
        {r.relationships.length > 0 ? (
          <>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-faint mt-5 mb-2">Relationships</h3>
            <ul className="space-y-1">
              {r.relationships.map((rel, i) => {
                const t = byId.get(rel.target_id)
                return <li key={i} className="text-[13px] flex items-center gap-2"><span className="text-muted">{humanKey(rel.kind)}</span>{t ? <button onClick={() => onSelect(t)} className="text-accent hover:underline inline-flex items-center gap-1"><ResourceIcon type={t.type} size={12} />{t.name}</button> : <span className="font-mono text-faint">{rel.target_id}</span>}</li>
              })}
            </ul>
          </>
        ) : null}
      </div>
    </div>
  )
}
