import { useEffect, useState, type ChangeEvent } from 'react'
import { CheckCircle2, RotateCcw } from 'lucide-react'
import type { HealthScoreSettings, HealthSeverity, HealthWeights } from '@/types'
import { getHealthScoreSettings, resetHealthScoreWeights, updateHealthScoreWeights } from '@/api'
import { useAsync } from '@/hooks/useAsync'
import { Badge, Button, Card, CardHeader, ErrorState, Field, Input, Skeleton } from '@/components/ui'

const SEVERITIES: Array<{ key: HealthSeverity; label: string; hint: string }> = [
  { key: 'critical', label: 'Critical weight', hint: 'Points lost when every object a check looked at has a critical finding.' },
  { key: 'warning', label: 'Warning weight', hint: 'Points lost when every object a check looked at has a warning.' },
  { key: 'info', label: 'Info weight', hint: 'Usually 0: informational findings do not move the score.' },
]

function problem(w: HealthWeights): string | null {
  for (const { key, label } of SEVERITIES) {
    const v = w[key]
    if (!Number.isInteger(v) || v < 0 || v > 100) return `${label} must be a whole number between 0 and 100.`
  }
  return null
}

// Health score weights. Saves on its own (separate endpoint from the main Settings form)
// so the score on the Overview moves as soon as a weight changes.
export default function HealthScoreCard() {
  const s = useAsync(() => getHealthScoreSettings(), [])
  const [weights, setWeights] = useState<HealthWeights>({ critical: 40, warning: 15, info: 0 })
  // What the server currently holds; dirty and Defaults are judged against this, not the initial load.
  const [applied, setApplied] = useState<HealthScoreSettings | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { if (s.data) { setWeights(s.data.weights); setApplied(s.data) } }, [s.data])

  const set = (k: HealthSeverity) => (e: ChangeEvent<HTMLInputElement>) => setWeights(w => ({ ...w, [k]: e.target.value === '' ? 0 : Math.floor(Number(e.target.value)) }))
  const apply = (d: HealthScoreSettings) => { setWeights(d.weights); setApplied(d); setSaved(true); setTimeout(() => setSaved(false), 2500) }
  const run = async (op: () => Promise<HealthScoreSettings>) => {
    setBusy(true); setErr(null); setSaved(false)
    try { apply(await op()) } catch (e) { setErr(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  const invalid = problem(weights)
  const dirty = applied ? SEVERITIES.some(({ key }) => applied.weights[key] !== weights[key]) : false
  const isDefault = applied ? SEVERITIES.every(({ key }) => applied.defaults[key] === weights[key]) : true

  return (
    <Card>
      <CardHeader title="Health score" subtitle="How much each severity costs on the Overview score. Applies to the next page load; no rescan needed."
        action={s.data ? <Badge tone={isDefault ? 'neutral' : 'accent'}>{isDefault ? 'Defaults' : 'Custom'}</Badge> : null} />
      <div className="px-5 pb-5 space-y-4">
        {s.error && !s.data ? <ErrorState title="Health score settings unavailable" error={s.error} onRetry={s.reload} />
          : !s.data ? <Skeleton className="h-24" /> : (
            <>
              <div className="grid sm:grid-cols-3 gap-4">
                {SEVERITIES.map(({ key, label, hint }) => (
                  <Field key={key} label={label} hint={hint}>
                    <Input type="number" min={0} max={100} inputMode="numeric" value={weights[key]} onChange={set(key)} disabled={busy} aria-label={label} />
                  </Field>
                ))}
              </div>
              <p className="text-sm text-muted">{s.data.formula}</p>
              <p className="text-xs text-faint">Example with these weights: one of four hosts disconnected costs {Math.round(weights.critical / 4)} points; one of forty costs {Math.round(weights.critical / 40)}. A check where every object fails costs its full weight.</p>
              {invalid ? <p className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2" role="alert">{invalid}</p> : null}
              {err ? <p className="text-sm text-critical bg-critical-bg rounded-md px-3 py-2" role="alert">{err}</p> : null}
              <div className="flex items-center gap-3">
                <Button variant="primary" onClick={() => void run(() => updateHealthScoreWeights(weights))} loading={busy} disabled={!dirty || invalid !== null}>Save weights</Button>
                <Button onClick={() => void run(() => resetHealthScoreWeights())} disabled={busy || isDefault}><RotateCcw size={14} /> Reset to defaults</Button>
                {saved ? <span className="text-sm text-ok inline-flex items-center gap-1.5"><CheckCircle2 size={15} /> Saved</span> : null}
              </div>
            </>
          )}
      </div>
    </Card>
  )
}
