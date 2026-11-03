// One definition of "the database is out" for every screen that has to say so:
// the auth status reports it the moment the page loads (before the readiness
// poll has answered), and readiness reports it from then on.
//
// When readiness could not be fetched at all (the backend is down and says nothing about
// its database) the console is what is unreachable, and an earlier auth status that said
// the database was out is stale: it must not put the blame on the database.
export function databaseOutage(
  authStatus: { database?: 'unavailable' } | null | undefined,
  databaseHealthy: boolean | null,
  backend: 'checking' | 'up' | 'down',
): boolean {
  if (databaseHealthy === null && backend === 'down') return false
  return authStatus?.database === 'unavailable' || databaseHealthy === false
}
