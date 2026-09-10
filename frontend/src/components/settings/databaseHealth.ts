type DatabaseTone = 'ok' | 'critical'

interface DatabaseHealthPresentation {
  label: string
  tone: DatabaseTone
  message: string
}

export function databaseHealthPresentation(healthy: boolean | null): DatabaseHealthPresentation | null {
  if (healthy === null) return null
  if (healthy) {
    return {
      label: 'Healthy',
      tone: 'ok',
      message: 'The connection is set by whoever deployed this instance and is not editable here.',
    }
  }
  return {
    label: 'Unavailable',
    tone: 'critical',
    message: 'The database connection is unavailable, so nothing is being recorded and history cannot be read. The connection is set by whoever deployed this instance; fix it there.',
  }
}
