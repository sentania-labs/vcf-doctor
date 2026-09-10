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
      message: 'The database connection is not editable here.',
    }
  }
  return {
    label: 'Not healthy',
    tone: 'critical',
    message: 'The database is not reporting healthy. Check the server log for the reason.',
  }
}
