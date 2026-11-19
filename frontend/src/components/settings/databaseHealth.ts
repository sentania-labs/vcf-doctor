type DatabaseTone = 'ok' | 'critical'

interface DatabaseHealthPresentation {
  label: string
  tone: DatabaseTone
  message: string
}

const CAUSES: Record<string, string> = {
  unmigrated: 'The console reaches its database, but the schema is not up to date. Run the migrations. This page updates when it is back.',
  unreachable: 'The database is unreachable. Check that PostgreSQL is running and reachable from the console.',
  credential_refused: 'The database refused the credential. Check the database user and password file in the deployment configuration.',
  database_missing: 'The database does not exist. Create it or correct the database name in the deployment configuration.',
  unknown: 'Could not connect to the database. Check the database and its deployment configuration.',
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
