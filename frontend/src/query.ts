// Things that arrive in the address bar rather than from the API: the emailed links
// (?reset=, ?verify=) and what came of a Google sign-in (?google=, PRD S16).
//
// Each is read once and taken straight back out of the URL, so a reload or a shared link
// cannot replay it.

/** A token from an emailed link, taken out of the address bar at once. */
export function takeToken(name: string): string | null {
  const params = new URLSearchParams(window.location.search)
  const token = params.get(name)
  if (token) {
    params.delete(name)
    const rest = params.toString()
    window.history.replaceState(null, '', `${window.location.pathname}${rest ? `?${rest}` : ''}${window.location.hash}`)
  }
  return token
}

/**
 * What came of a Google sign-in (S16). The callback is a browser navigation, not a fetch,
 * so its answer comes back as ?google= and would otherwise be swallowed: a refused sign-in
 * would look like nothing at all having happened.
 */
const GOOGLE_SAID: Record<string, string> = {
  expired: 'That took too long, or the sign-in did not start here. Try again.',
  failed: "Google sign-in didn't work. Try again.",
  email_unverified: 'Google has not confirmed that address. Sign in with your password, then connect Google from Settings.',
  account_off: 'This account is turned off. Ask the JAP admin.',
  sign_up_closed: 'New accounts are paused for now. Try again later.',
  google_taken: 'That Google account is already on another JAP account.',
}

export function googleOutcome(): { problem: string | null; done: string | null } {
  const said = takeToken('google')
  if (!said) return { problem: null, done: null }
  if (said === 'signed_in' || said === 'connected') return { problem: null, done: said }
  return { problem: GOOGLE_SAID[said] ?? "Google sign-in didn't work. Try again.", done: null }
}
