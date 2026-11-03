// The console's data providers load once, when they mount. One that mounted
// while the database was out holds the failed loads (no connections, no scans)
// and nothing asks again, so the console must not carry them across the
// outage. Keying the providers on this value remounts them, and so reloads
// everything, the moment the database is reachable again.
export function consoleSession(status: { database?: 'unavailable' } | null): 'outage' | 'live' {
  return status?.database === 'unavailable' ? 'outage' : 'live'
}
