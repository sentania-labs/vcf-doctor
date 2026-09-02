// Mirrors backend/app/models. Keep in sync; do not rename fields.
export type Severity = 'info' | 'warning' | 'critical'
export type Significance = 'low' | 'medium' | 'high'
export type ChangeType = 'added' | 'removed' | 'modified'
export type ScanStatus = 'running' | 'ok' | 'error' | 'skipped'

export interface Relationship { kind: string; target_id: string }
export interface Resource {
  id: string; type: string; name: string; source: string
  parent_id: string | null; properties: Record<string, unknown>; relationships: Relationship[]
}
export interface Finding {
  id: string; check_id: string; severity: Severity; title: string; summary: string
  resource_id: string | null; resource_name: string | null; resource_type: string | null
  evidence: Record<string, unknown>; recommendation: string | null
}
export interface PropertyChange { old: unknown; new: unknown }
export interface Change {
  change_type: ChangeType; resource_id: string; resource_type: string; resource_name: string
  property_changes: Record<string, PropertyChange>; significance: Significance; summary: string
}
// Retention tier (docs/RETENTION_EVENTS.md): manual snapshots are never pruned.
export type SnapshotTier = 'manual' | 'recent' | 'hourly' | 'daily'
export interface SnapshotSummary {
  id: string; created_at: string; label: string; connection_id: string
  scheduled: boolean; resource_count: number; tier: SnapshotTier
}
// A persisted diff row from GET /changes/log (newest first). observed_at is the TO snapshot time.
export type ChangeLogEntry = Change & { id: string; observed_at: string; from_snapshot_id: string; to_snapshot_id: string }
// vCenter event or task, normalised by the collector and stored per connection.
export type EventSource = 'event' | 'task'
export type EventCategory = 'info' | 'warning' | 'error' | 'user'
export interface Event {
  id: string; connection_id: string; time: string; source: EventSource; type: string; category: EventCategory
  message: string; user: string | null; resource_id: string | null; resource_name: string | null; resource_type: string | null
}
export interface Snapshot extends SnapshotSummary { resources: Resource[] }
export interface ConnectionPublic {
  id: string; name: string; host: string; username: string; verify_tls: boolean
  created_at: string; kind: string
}
export interface ConnectionCreate {
  name: string; host: string; username: string; password: string
  verify_tls: boolean; interval_minutes: number; enabled: boolean
}
export interface Schedule {
  connection_id: string; interval_minutes: number; enabled: boolean
  last_run: string | null; next_run: string | null; last_status: ScanStatus | null
}
export interface ScanRun {
  id: string; connection_id: string; started: string; finished: string | null
  status: ScanStatus; error: string | null; snapshot_id: string | null; trigger: 'scheduled' | 'manual'
}
export type AssistantTask = 'explain' | 'investigate' | 'generate-script' | 'ask'
export type ScriptFormat = 'powercli' | 'python' | 'shell' | 'rest'
export interface AssistantContext {
  question: string; findings: Finding[]; changes: Change[]; resources: Resource[]; events: Event[]; allowed_actions: string[]
}
export interface AssistantRequest { task: AssistantTask; script_format?: ScriptFormat; context: AssistantContext }
export interface AssistantSettings { enabled: boolean; provider: 'anthropic' | 'mock'; model: string; api_key_set: boolean }
export interface AssistantStatus { available: boolean; provider: string; model: string; reason: string | null }

// ---- Frontend-added types (Agent D). Shapes assumed from the API notes; field names above are frozen.
export interface OverviewCounts { critical: number; warning: number; info: number; passed: number }
export interface OverviewResources { total: number; by_type: Record<string, number> }
// Health score breakdown. weights: per-severity maximum deduction when every evaluated object fails a check.
export type HealthSeverity = 'critical' | 'warning' | 'info'
export type HealthWeights = Record<HealthSeverity, number>
export interface HealthCheckLine { check_id: string; evaluated: number; findings: number; deduction: number }
export interface HealthBreakdown {
  score: number
  passed: number
  findings: number
  not_evaluated: number
  deduction: number
  weights: HealthWeights
  formula: string
  checks: HealthCheckLine[]
}
export interface HealthScoreSettings { weights: HealthWeights; defaults: HealthWeights; formula: string }
export interface Overview {
  health_score: number
  health: HealthBreakdown
  counts: OverviewCounts
  resources: OverviewResources
  hosts_connected: number
  hosts_total: number
  vms_on: number
  vms_total: number
  storage_free_pct: number | null
  last_scan: string | null
  top_findings: Finding[]
  recent_changes: Change[]
}
export interface ConnectionTestResult { ok: boolean; message: string; version?: string | null; build?: string | null }
// Retention policy in days per tier; must satisfy recent_days <= hourly_days <= daily_days.
// Events and the change log follow daily_days.
export interface RetentionPolicy { recent_days: number; hourly_days: number; daily_days: number }
// changes_min_significance: lowest significance the Changes page and Overview show by default (low = everything).
export interface Settings { retention_policy: RetentionPolicy; assistant: AssistantSettings; changes_min_significance?: Significance }
export interface SettingsUpdate { retention_policy: RetentionPolicy; assistant: Partial<AssistantSettings> & { api_key?: string }; changes_min_significance?: Significance }
export interface AssistantEvidenceCount { findings: number; changes: number; resources: number; events: number }
export type AssistantStreamEvent =
  | { type: 'delta'; text: string }
  | { type: 'done'; stop_reason: string; evidence: AssistantEvidenceCount }
  | { type: 'error'; message: string }
// GET /environment/changes: what changed across every connection between two points in time.
export interface SignificanceCounts { high: number; medium: number; low: number; total: number }
// Findings present at the end of the window but not at its start (appeared), and the reverse (cleared),
// compared between the findings cached with the two boundary snapshots.
export interface FindingsDelta {
  baseline_snapshot_id: string; baseline_at: string; end_snapshot_id: string; end_at: string
  appeared: Finding[]; cleared: Finding[]
}
export interface EnvironmentConnection {
  connection_id: string; name: string; host: string; kind: string
  has_data: boolean; snapshots_in_window: number; counts: SignificanceCounts
  changes: ChangeLogEntry[]; truncated: boolean; findings: FindingsDelta | null
}
export interface EnvironmentTotals {
  connections: number; covered: number; no_data: number; changes: SignificanceCounts
  findings_appeared: number; findings_cleared: number
}
export interface EnvironmentChanges {
  since: string; until: string; window: 'last_cycle' | 'custom'; min_significance: Significance
  totals: EnvironmentTotals; connections: EnvironmentConnection[]
}
