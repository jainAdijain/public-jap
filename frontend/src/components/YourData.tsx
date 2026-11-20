import { useState, type FormEvent } from 'react'

import { auth, type User } from '../api/client'
import { errorText } from '../format'

// Getting your data back, and leaving (PRD S15). Shared, because it belongs in two places:
// Settings > Account, and the agreement screen — someone who will not agree to the notice
// still has to be able to take their data and go (App.tsx).

/** Download everything JAP holds about this account, as a zip. */
export function ExportMyData({ onProblem }: { onProblem: (text: string) => void }) {
  const [busy, setBusy] = useState(false)
  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => {
        setBusy(true)
        auth
          .exportMyData()
          .catch((err: unknown) => onProblem(errorText(err)))
          .finally(() => setBusy(false))
      }}
    >
      {busy ? 'Making your zip…' : 'Download my data'}
    </button>
  )
}

/**
 * Delete my account. Three things are asked for, because it happens at once and there is
 * no undo: the password, the code when two-step sign-in is on, and the address typed out.
 */
export function DeleteMyAccount({ user, onDeleted }: { user: User; onDeleted: () => void }) {
  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const matches = typed.trim().toLowerCase() === user.email.toLowerCase()
  const close = () => {
    setOpen(false)
    setPassword('')
    setCode('')
    setTyped('')
    setError(null)
  }
  if (!open) {
    return (
      <button type="button" className="btn-danger" onClick={() => setOpen(true)}>
        Delete my account
      </button>
    )
  }
  return (
    <form
      className="account-form"
      onSubmit={(e: FormEvent) => {
        e.preventDefault()
        setError(null)
        setBusy(true)
        auth
          .deleteMyAccount(password, typed.trim(), code)
          .then(onDeleted)
          .catch((err: unknown) => setError(errorText(err)))
          .finally(() => setBusy(false))
      }}
    >
      <p className="warn-text small">
        This deletes your profile, resumes, answers, applications, searches and every file, at once and for good. There is no undo and nothing is kept to restore. Applications already sent cannot be
        unsent. Download your data first if you want it.
      </p>
      <label className="auth-field">
        <span>Your password</span>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
      </label>
      {user.two_step && (
        <label className="auth-field">
          <span>Your 6-digit code</span>
          <input type="text" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} required />
        </label>
      )}
      <label className="auth-field">
        <span>
          Type <b>{user.email}</b> to confirm
        </span>
        <input type="text" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} required />
      </label>
      {error && (
        <p className="error-text small" role="alert">
          {error}
        </p>
      )}
      <div className="inline">
        <button type="submit" className="btn-danger" disabled={busy || !matches || !password}>
          {busy ? 'Deleting…' : 'Delete my account for good'}
        </button>
        <button type="button" className="btn-quiet" onClick={close}>
          Cancel
        </button>
      </div>
    </form>
  )
}
