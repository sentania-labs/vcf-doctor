// One definition of "the database is out" for every screen that has to say so:
// the auth status reports it the moment the page loads (before the readiness
// poll has answered), and readiness reports it from then on.
export function databaseOutage(
  authStatus: { database?: 'unavailable' } | null | undefined,
  databaseHealthy: boolean | null,
): boolean {
  return authStatus?.database === 'unavailable' || databaseHealthy === false
}

// A 401 from the API means "signed out" only if the database is up. With it
// gone a session cannot be verified, so the same 401 reaches someone who is
// signed in; the auth status says which it is. Returns the status when it
// reports an outage, otherwise null, and so does a status call that fails.
export async function outageBehindUnauthorized<S extends { database?: 'unavailable' }>(
  fetchStatus: () => Promise<S>,
): Promise<S | null> {
  try {
    const s = await fetchStatus()
    return s.database === 'unavailable' ? s : null
  } catch {
    return null
  }
}
