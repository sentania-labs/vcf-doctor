type DatabaseTone = 'ok' | 'critical'

interface DatabaseHealthPresentation {
  label: string
  tone: DatabaseTone
  message: string
}

const CAUSES: Record<string, string> = {
  unmigrated: 'The console reaches its database, but the schema is not up to date. Run the migrations. This page updates when it is back.',
}

export function databaseHealthPresentation(healthy: boolean | null, reason?: string): DatabaseHealthPresentation | null {
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
    message: (reason && CAUSES[reason]) || 'The console cannot use its database. Check that PostgreSQL is running and reachable from the console, and that its schema is up to date. This page updates when it is back.',
  }
}
