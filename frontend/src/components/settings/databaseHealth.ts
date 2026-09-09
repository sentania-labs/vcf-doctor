type DatabaseTone = 'neutral' | 'ok' | 'critical'

interface DatabaseHealthPresentation {
  label: string
  tone: DatabaseTone
  dot: boolean
  message: string
}

export function databaseHealthPresentation(healthy: boolean | null): DatabaseHealthPresentation {
  if (healthy === null) {
    return {
      label: 'Cannot tell',
      tone: 'neutral',
      dot: false,
      message: 'This console cannot currently report database health because the backend did not answer.',
    }
  }
  if (healthy) {
    return {
      label: 'Healthy',
      tone: 'ok',
      dot: true,
      message: 'The connection is set by whoever deployed this instance and is not editable here.',
    }
  }
  return {
    label: 'Unavailable',
    tone: 'critical',
    dot: true,
    message: 'The database connection is unavailable, so nothing is being recorded and history cannot be read. The connection is set by whoever deployed this instance; fix it there.',
  }
}
