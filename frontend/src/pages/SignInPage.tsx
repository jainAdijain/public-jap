import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'

import { ApiError, auth, type AuthState, type User } from '../api/client'
import { errorText } from '../format'
import PrivacyNotice from './PrivacyPage'

// The screen shown before JAP opens (PRD S1 to S6, S10): create an account, sign in, the
// two-step code, Forgot password and the reset link from the email. Anyone can create an
// account. The first one is the admin and goes straight in; every one after that gets an
// emailed link and sees Check your email until it is opened.

type Mode =
  | 'sign-in'
  | 'create'
  | 'code'
  | 'forgot'
  | 'forgot-sent'
  | 'reset'
  | 'reset-done'
  | 'verify-sent'
  | 'verifying'
  | 'verify-failed'
  | 'notice'

const MIN_PASSWORD = 12

function Card({ title, lead, wide, children }: { title: string; lead?: string; wide?: boolean; children?: ReactNode }) {
  return (
    <main className="auth">
      <div className={wide ? 'auth-card wide' : 'auth-card'}>
        <div className="logo auth-logo">
          <span className="logo-mark">JAP</span>
          <span className="logo-name">Job Application Pipeline</span>
        </div>
        {title && <h1>{title}</h1>}
        {lead && <p className="muted">{lead}</p>}
        {children}
      </div>
    </main>
  )
}

/**
 * Sign in with Google (S16). A link, not a fetch: the browser itself has to go to Google
 * and come back, which XHR cannot do. Shown only where the server says it is set up.
 */
export function GoogleButton({ show, label }: { show: boolean; label: string }) {
  if (!show) return null
  return (
    <>
      <hr className="rule" />
      <a className="btn auth-submit" href={auth.googleStartUrl}>
        {/* Google's own G, so the button is recognisable at a glance. */}
        <svg viewBox="0 0 48 48" width="18" height="18" aria-hidden="true">
          <path fill="#4285F4" d="M45 24c0-1.6-.1-2.7-.4-4H24v7.5h12c-.2 2-1.6 5-4.5 7l6.9 5.4C42.5 36.2 45 30.6 45 24z" />
          <path fill="#34A853" d="M24 46c5.9 0 10.9-2 14.5-5.3l-6.9-5.4c-1.9 1.3-4.400 2.1-7.6 2.1-5.8 0-10.7-3.9-12.5-9.1l-7.1 5.5C8 40.4 15.4 46 24 46z" />
          <path fill="#FBBC05" d="M11.5 28.3A13.6 13.6 0 0 1 10.8 24c0-1.5.3-3 .7-4.3l-7.1-5.5A22 22 0 0 0 2 24c0 3.5.8 6.9 2.4 9.8z" />
          <path fill="#EA4335" d="M24 10.8c3.3 0 5.5 1.4 6.8 2.6l5-4.9C32.9 5.7 29.9 4 24 4 15.4 4 8 9.6 4.4 14.2l7.1 5.5C13.3 14.7 18.2 10.8 24 10.8z" />
        </svg>
        {label}
      </a>
    </>
  )
}

/** The terms and privacy tick (S14), on sign-up and on the agreement screen. */
export function TermsTick({ checked, onChange, onRead }: { checked: boolean; onChange: (v: boolean) => void; onRead: () => void }) {
  return (
    <label className="tick">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>
        I agree to the{' '}
        <button type="button" className="btn-link" onClick={onRead}>
          terms and privacy notice
        </button>
        .
      </span>
    </label>
  )
}

function Field(props: {
  label: string
  type: string
  value: string
  onChange: (v: string) => void
  autoComplete: string
  hint?: string
  inputMode?: 'numeric' | 'text'
  autoFocus?: boolean
}) {
  return (
    <label className="auth-field">
      <span>{props.label}</span>
      <input
        type={props.type}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        autoComplete={props.autoComplete}
        inputMode={props.inputMode}
        autoFocus={props.autoFocus}
        required
      />
      {props.hint && <small className="muted">{props.hint}</small>}
    </label>
  )
}

/** A token from an emailed link (?reset=... or ?verify=...), taken out of the address bar at once. */
function takeToken(name: string): string | null {
  const params = new URLSearchParams(window.location.search)
  const token = params.get(name)
  if (token) {
    params.delete(name)
    const rest = params.toString()
    window.history.replaceState(null, '', `${window.location.pathname}${rest ? `?${rest}` : ''}${window.location.hash}`)
  }
  return token
}

type TurnstileApi = {
  render: (el: HTMLElement, options: { sitekey: string; callback: (token: string) => void; 'expired-callback': () => void }) => string
  remove: (id: string) => void
}
const TURNSTILE_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'
const turnstileApi = () => (window as unknown as { turnstile?: TurnstileApi }).turnstile

/** Cloudflare's "prove you're human" check, shown only when the server has a site key. */
function Turnstile({ siteKey, onToken }: { siteKey: string; onToken: (token: string | null) => void }) {
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let id: string | null = null
    let gone = false
    const show = () => {
      const api = turnstileApi()
      if (gone || !api || !box.current) return
      id = api.render(box.current, { sitekey: siteKey, callback: onToken, 'expired-callback': () => onToken(null) })
    }
    if (turnstileApi()) show()
    else {
      let script = document.querySelector<HTMLScriptElement>(`script[src="${TURNSTILE_SRC}"]`)
      if (!script) {
        script = document.createElement('script')
        script.src = TURNSTILE_SRC
        script.async = true
        document.head.appendChild(script)
      }
      script.addEventListener('load', show)
    }
    return () => {
      gone = true
      if (id) turnstileApi()?.remove(id)
      onToken(null)
    }
  }, [siteKey, onToken])
  return <div ref={box} className="auth-human" />
}

export default function SignInPage({ state, onSignedIn }: { state: AuthState; onSignedIn: (u: User) => void }) {
  const [resetToken] = useState(() => takeToken('reset'))
  const [verifyToken] = useState(() => takeToken('verify'))
  const first = !state.has_accounts // the first account is the admin and needs no email check
  const [mode, setMode] = useState<Mode>(() =>
    resetToken ? 'reset' : verifyToken ? 'verifying' : state.two_step_pending ? 'code' : first ? 'create' : 'sign-in',
  )
  const [human, setHuman] = useState<string | null>(null)
  const [agreed, setAgreed] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const needsHuman = Boolean(state.turnstile_site_key)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [again, setAgain] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // The emailed link (S10): confirm the address and go in. The link works once, so this
  // runs once even when React mounts the page twice in development.
  const verifying = useRef(false)
  useEffect(() => {
    if (!verifyToken || verifying.current) return
    verifying.current = true
    auth
      .verify(verifyToken)
      .then((res) => {
        if (res.user) onSignedIn(res.user)
      })
      .catch((err) => {
        setError(errorText(err))
        setMode('verify-failed')
      })
  }, [verifyToken, onSignedIn])

  const go = (m: Mode) => {
    setError(null)
    setNotice(null)
    setPassword('')
    setAgain('')
    setCode('')
    setMode(m)
  }
  const run = (fn: () => Promise<void>) => async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await fn()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }
  const newPasswordProblem = () => {
    if (password.length < MIN_PASSWORD) return `Use at least ${MIN_PASSWORD} characters`
    if (password !== again) return 'The two passwords are different'
    return null
  }
  const failure = error && (
    <p className="error-text small" role="alert">
      {error}
    </p>
  )
  const submit = (label: string) => (
    <button type="submit" className="btn-primary auth-submit" disabled={busy}>
      {busy ? 'One moment…' : label}
    </button>
  )

  const humanCheck = needsHuman && <Turnstile siteKey={state.turnstile_site_key} onToken={setHuman} />
  const humanProblem = () => (needsHuman && !human ? "Tick the check that you're human first" : null)

  // The notice itself, read from the sign-up form and shown before anyone types anything.
  if (mode === 'notice') {
    return (
      <Card title="" wide>
        <PrivacyNotice />
        <button type="button" className="btn-primary auth-submit" onClick={() => setMode('create')}>
          Back to creating your account
        </button>
      </Card>
    )
  }

  if (mode === 'create') {
    return (
      <Card
        title="Create your JAP account"
        lead={first ? 'This first account is the admin of this JAP.' : "We'll email you a link to confirm your address before you can sign in."}
      >
        <form
          className="auth-form"
          onSubmit={run(async () => {
            const problem = newPasswordProblem() ?? humanProblem() ?? (agreed ? null : 'Agree to the terms and the privacy notice first')
            if (problem) throw new Error(problem)
            const res = await auth.signUp(email, password, state.terms_version, human)
            if (res.user) onSignedIn(res.user)
            else if (res.verify_email) go('verify-sent')
          })}
        >
          <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" autoFocus />
          <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="new-password" hint={`At least ${MIN_PASSWORD} characters.`} />
          <Field label="Password again" type="password" value={again} onChange={setAgain} autoComplete="new-password" />
          <TermsTick checked={agreed} onChange={setAgreed} onRead={() => setMode('notice')} />
          {humanCheck}
          {failure}
          {submit('Create account')}
          {!first && (
            <button type="button" className="btn-link" onClick={() => go('sign-in')}>
              I already have an account
            </button>
          )}
          {/* Google asks for the same agreement, on the screen straight after signing in. */}
          <GoogleButton show={state.google} label="Create your account with Google" />
        </form>
      </Card>
    )
  }

  if (mode === 'verify-sent') {
    return (
      <Card
        title="Check your email"
        lead={`We sent a link to ${email}. Open it to confirm your address and sign in. It works once, for 24 hours. If ${email} already has a JAP account, sign in instead.`}
      >
        <form
          className="auth-form"
          onSubmit={run(async () => {
            setNotice(null)
            await auth.resendVerification(email)
            setNotice('Sent again, if a link was still waiting. One email goes out every 2 minutes at most.')
          })}
        >
          {notice && <p className="muted small">{notice}</p>}
          {failure}
          {submit('Send again')}
          <button type="button" className="btn-link" onClick={() => go('sign-in')}>
            Back to sign in
          </button>
        </form>
      </Card>
    )
  }

  if (mode === 'verifying') {
    return <Card title="Confirming your email" lead="One moment…" />
  }

  if (mode === 'verify-failed') {
    return (
      <Card title="That link didn't work" lead={error ?? 'This link has expired or was already used.'}>
        <button type="button" className="btn-primary auth-submit" onClick={() => go('sign-in')}>
          Sign in
        </button>
      </Card>
    )
  }

  if (mode === 'code') {
    return (
      <Card title="Two-step sign-in" lead="Enter the 6-digit code from your authenticator app, or one of your recovery codes.">
        <form
          className="auth-form"
          onSubmit={run(async () => {
            const res = await auth.code(code)
            if (res.user) onSignedIn(res.user)
          })}
        >
          <Field label="Code" type="text" value={code} onChange={setCode} autoComplete="one-time-code" inputMode="text" autoFocus />
          {failure}
          {submit('Sign in')}
          <button type="button" className="btn-link" onClick={() => go('sign-in')}>
            Start again with your password
          </button>
        </form>
      </Card>
    )
  }

  if (mode === 'forgot') {
    return (
      <Card title="Forgot your password?" lead="We'll email you a link to set a new one. It works once, for 30 minutes.">
        <form
          className="auth-form"
          onSubmit={run(async () => {
            const problem = humanProblem()
            if (problem) throw new Error(problem)
            await auth.forgot(email, human)
            go('forgot-sent')
          })}
        >
          <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" autoFocus />
          {humanCheck}
          {failure}
          {submit('Email me a link')}
          <button type="button" className="btn-link" onClick={() => go('sign-in')}>
            Back to sign in
          </button>
        </form>
      </Card>
    )
  }

  if (mode === 'forgot-sent') {
    return (
      <Card title="Check your email" lead={`If ${email} has a JAP account, a link to set a new password is on its way. It works for 30 minutes.`}>
        <button type="button" className="btn-link" onClick={() => go('sign-in')}>
          Back to sign in
        </button>
      </Card>
    )
  }

  if (mode === 'reset') {
    return (
      <Card title="Set a new password" lead="Every browser signed in to your account will be signed out.">
        <form
          className="auth-form"
          onSubmit={run(async () => {
            const problem = newPasswordProblem()
            if (problem) throw new Error(problem)
            await auth.reset(resetToken ?? '', password)
            go('reset-done')
          })}
        >
          <Field label="New password" type="password" value={password} onChange={setPassword} autoComplete="new-password" hint={`At least ${MIN_PASSWORD} characters.`} autoFocus />
          <Field label="New password again" type="password" value={again} onChange={setAgain} autoComplete="new-password" />
          {failure}
          {submit('Set password')}
          <button type="button" className="btn-link" onClick={() => go('forgot')}>
            Ask for a new link
          </button>
        </form>
      </Card>
    )
  }

  if (mode === 'reset-done') {
    return (
      <Card title="Password changed" lead="Sign in with your new password.">
        <button type="button" className="btn-primary auth-submit" onClick={() => go('sign-in')}>
          Sign in
        </button>
      </Card>
    )
  }

  return (
    <Card title="Sign in to JAP">
      <form
        className="auth-form"
        onSubmit={run(async () => {
          try {
            const res = await auth.signIn(email, password)
            if (res.two_step) go('code')
            else if (res.user) onSignedIn(res.user)
          } catch (err) {
            // The address is not confirmed yet (S10): the page to send the link again.
            if (err instanceof ApiError && err.code === 'email_unverified') go('verify-sent')
            else throw err
          }
        })}
      >
        <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="username" autoFocus />
        <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="current-password" />
        {failure}
        {submit('Sign in')}
        <button type="button" className="btn-link" onClick={() => go('forgot')}>
          Forgot password?
        </button>
        {state.sign_up_open ? (
          <button type="button" className="btn-link" onClick={() => go('create')}>
            Create an account
          </button>
        ) : (
          <p className="muted small">New accounts are paused for now.</p>
        )}
        <GoogleButton show={state.google} label="Continue with Google" />
      </form>
    </Card>
  )
}
