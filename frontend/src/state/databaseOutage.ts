// One definition of "the database is out" for every screen that has to say so:
// the auth status reports it the moment the page loads (before the readiness
// poll has answered), and readiness reports it from then on.
export function databaseOutage(
  authStatus: { database?: 'unavailable' } | null | undefined,
  databaseHealthy: boolean | null,
): boolean {
  return authStatus?.database === 'unavailable' || databaseHealthy === false
}
