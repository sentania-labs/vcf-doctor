// What the login page says when the backend sends a browser back with ?sso=<code>.
// The code is looked up, never shown: it is part of the URL and anyone can type one.
const MESSAGES: Record<string, string> = {
  unavailable: 'Single sign-on is unavailable right now: the identity provider did not answer. Try again in a moment, or sign in with the operator password.',
  not_authorized: 'You signed in, but your account is not in a group that may use VCF Doctor. Ask an administrator to add you, or sign in with the operator password.',
  denied: 'The identity provider did not approve the sign-in.',
  expired: 'That sign-in attempt expired or did not start in this browser. Start again.',
  not_configured: 'Single sign-on is not set up on this deployment.',
  failed: 'Single sign-on failed: the identity provider\'s response could not be verified. Start again, or sign in with the operator password.',
}

export function ssoMessage(code: string | null): string | null {
  if (!code) return null
  return Object.hasOwn(MESSAGES, code) ? MESSAGES[code] : MESSAGES.failed
}
