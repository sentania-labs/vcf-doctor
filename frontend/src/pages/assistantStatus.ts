import type { AssistantStatus } from '../types/index.ts'

export function assistantBadge(status: AssistantStatus | null, loading: boolean, error: Error | null): { label: string; tone: 'neutral' | 'warning' | 'ok' } | null {
  if (loading) return { label: 'Checking', tone: 'neutral' }
  if (error) return { label: 'Status unknown', tone: 'warning' }
  if (!status) return null
  return status.available ? { label: 'Configured', tone: 'ok' } : { label: 'Unavailable', tone: 'warning' }
}
