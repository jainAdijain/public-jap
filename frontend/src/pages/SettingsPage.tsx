import { useEffect, useState, type InputHTMLAttributes, type ReactNode } from 'react'

import { admin, api, type Account, type Accounts, type Run, type Status, type Switches } from '../api/client'
import { atsLabel, errorText, money, planWords, STARTED_BY, when } from '../format'
import { useApp } from '../appContext'
import { useAuth } from '../authContext'
import AccountSection from './AccountSection'

const hhmm = (t: string) => t.slice(0, 5)
const size = (bytes: number) =>
  bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : bytes < 1024 ** 3 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${(bytes / 1024 ** 3).toFixed(1)} GB`

/** A setting you type rather than tick (JAP-6).
 *
 * Everything else on this page saves the moment it is clicked, and these did too — but only
 * when the field lost focus. With no button and no answer to Enter, a typed number looked
 * ignored until you happened to click somewhere else. Enter saves, so does the Save button
 * that appears as soon as the value differs from the saved one, Escape forgets it, and
 * leaving the field still saves so nothing typed is lost. What was typed stays on screen
 * until the save comes back, or a slow connection would show the old value again in between.
 */
function Typed({
  saved,
  commit,
  children,
  ...rest
}: {
  saved: string
  commit: (value: string) => Promise<void>
  children?: ReactNode
} & Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'onBlur' | 'onKeyDown' | 'children'>) {
  const [draft, setDraft] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const pending = draft !== null && draft !== saved
  const apply = async () => {
    if (!pending) {
      setDraft(null)
      return
    }
    setSaving(true)
    await commit(draft)
    setSaving(false)
    setDraft(null)
  }
  return (
    <>
      <input
        {...rest}
        value={draft ?? saved}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            void apply()
          }
          if (e.key === 'Escape') setDraft(null)
        }}
        onBlur={() => void apply()}
      />
      {children}
      {pending && (
        // mousedown would blur the field first and take the button away before the click.
        <button type="button" className="btn-primary btn-sm" disabled={saving} onMouseDown={(e) => e.preventDefault()} onClick={() => void apply()}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      )}
    </>
  )
}

function problems(run: Run): [string, string] {
  if (run.status === 'failed') return [run.error ?? 'The search failed', 'error-text']
  if (run.status === 'running') return ['Searching now…', 'muted']
  if (run.status === 'queued') return ['Waiting to start', 'muted']
  const c = run.counts
  const out: string[] = []
  if (c.board_errors?.length) out.push(...c.board_errors)
  else if (c.boards_failed) out.push(`${c.boards_failed} companies could not be read`)
  if (c.spend_cap_reached) out.push('AI limit reached')
  if (c.daily_cap_reached) out.push('Daily sending limit reached')
  if (c.draft_failed) out.push(`${c.draft_failed} answers not written (AI model failed)`)
  return out.length ? [out.join('; '), 'warn-text'] : ['None', 'muted']
}

function SearchHistory() {
  const { tick } = useApp()
  const [runs, setRuns] = useState<Run[] | null>(null)
  useEffect(() => {
    api.runs().then(setRuns, () => setRuns([]))
  }, [tick])
  return (
    <div className="sec">
      <div className="sec-l">
        <h2>Search history</h2>
        <p>Every search for new jobs, what started it and what it found. JAP also searches after you save Job preferences or use the skills from a new resume.</p>
      </div>
      <div className="sec-r flush">
        {runs && runs.length === 0 ? (
          <p className="muted" style={{ padding: 20 }}>
            No searches yet.
          </p>
        ) : (
          <div className="table-scroll">
            <table className="hist">
              <colgroup>
                <col style={{ width: '16%' }} />
                <col style={{ width: '15%' }} />
                <col style={{ width: '18%' }} />
                <col style={{ width: '9%' }} />
                <col style={{ width: '8%' }} />
                <col style={{ width: '10%' }} />
                <col style={{ width: '24%' }} />
              </colgroup>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Started by</th>
                  <th>Job titles</th>
                  <th>Found</th>
                  <th>New</th>
                  <th>Applied</th>
                  <th>Problems</th>
                </tr>
              </thead>
              <tbody>
                {(runs ?? []).map((r) => {
                  const [text, cls] = problems(r)
                  const roles = r.scope.roles?.length ? r.scope.roles.join(', ') : 'All'
                  return (
                    <tr key={r.id}>
                      <td className="mono">{when(r.started_at ?? r.created_at)}</td>
                      <td>
                        {STARTED_BY[r.trigger]}
                        {r.mode === 'find_and_apply' && <span className="muted"> · and apply</span>}
                      </td>
                      <td title={roles}>{roles}</td>
                      <td className="mono">{r.counts.fetched ?? '–'}</td>
                      <td className="mono">{r.counts.new_listings ?? r.counts.new ?? '–'}</td>
                      <td className="mono">{r.mode === 'find_and_apply' ? (r.counts.applied ?? 0) : '–'}</td>
                      <td className={cls} title={text}>
                        {text}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function SystemStatus() {
  const { health, healthError, ops, toast } = useApp()
  const [shot, setShot] = useState<string | null>(null)
  const start = async (what: 'backup' | 'forms') => {
    try {
      await (what === 'backup' ? api.backupNow() : api.formCheckNow())
      toast(what === 'backup' ? 'Backing up. The file shows here in a few seconds.' : 'Checking one real form on each system. Nothing is submitted.')
    } catch (err) {
      toast({ text: errorText(err), bad: true })
    }
  }
  const check = (k: string, name: string, okText: string, badText: string) => {
    const c = health?.checks[k]
    const ok = healthError ? false : c ? c.ok : null
    const detail = healthError ? (k === 'api' ? `Not answering: ${healthError}` : 'Unknown while the app server is down') : c ? (c.ok ? okText : `${badText}${c.detail ? ` (${c.detail})` : ''}`) : 'Checking…'
    return (
      <div key={k} className="hrow">
        <i className={ok === null ? '' : ok ? 'ok' : 'bad'} />
        <b>{name}</b>
        <span className="hd muted">{detail}</span>
      </div>
    )
  }
  const checks = ops ? new Map(ops.form_checks.map((c) => [c.ats, c])) : null
  const failed = ops?.form_checks.filter((c) => !c.ok) ?? []
  const lastCheck = ops?.form_checks.map((c) => c.checked_at).sort().pop()
  return (
    <div className="sec">
      <div className="sec-l">
        <h2>System status</h2>
        <p>Checked every few seconds.</p>
      </div>
      <div className="sec-r">
        <div className="health">
          {check('api', 'App server', 'Running', 'Not running')}
          {check('database', 'Database', 'Connected', 'Not connected')}
          {check('inngest', 'Job search', 'Ready', 'Not ready')}
          {check('ollama', 'Local AI', 'Running', 'Not running. Start Ollama.')}
          <div className="hrow">
          <i className={ops ? (ops.backup_today ? 'ok' : 'bad') : ''} />
          <b>Backup</b>
          <span className="hd muted">
            {!ops
              ? 'Checking…'
              : ops.last_backup
                ? `${ops.backup_today ? 'Today' : 'None today yet; last'}: ${ops.last_backup.file} (${size(ops.last_backup.bytes)}, ${when(ops.last_backup.at)}). ${ops.backups} kept for 14 days.`
                : 'No backup yet'}
          </span>
          <button type="button" onClick={() => void start('backup')}>
            Back up now
          </button>
          </div>
          <div className="hrow">
          <i className={!checks || checks.size === 0 ? '' : failed.length ? 'bad' : 'ok'} />
          <b>Form check</b>
          <span className="hd muted">
            {!checks || checks.size === 0
              ? 'Weekly, Mondays 07:00. Not run yet.'
              : (['greenhouse', 'lever', 'ashby'] as const)
                  .map((a) => {
                    const c = checks.get(a)
                    return c ? `${atsLabel(a)} ${c.ok ? `OK (${c.fields} fields)` : `failed: ${c.error ?? 'unknown'}`}` : `${atsLabel(a)} not checked`
                  })
                  .join(' · ') + (lastCheck ? `. Checked ${when(lastCheck)}.` : '')}
            {failed
              .filter((c) => c.has_screenshot)
              .map((c) => (
                <button key={c.id} type="button" className="btn-link" style={{ marginLeft: 8 }} onClick={() => setShot(shot === c.id ? null : c.id)}>
                  {shot === c.id ? 'Hide screenshot' : `${atsLabel(c.ats)} screenshot`}
                </button>
              ))}
          </span>
          <button type="button" onClick={() => void start('forms')}>
            Check now
          </button>
          </div>
        </div>
        {shot && <img className="shot" style={{ maxWidth: '100%' }} src={api.formCheckShotUrl(shot)} alt="The form page when the check failed" />}
      </div>
    </div>
  )
}

const ALERT_NAMES: Record<string, string> = {
  'daily-missed': 'Daily search did not start',
  'stuck-run': 'A search was stuck',
  errors: 'Many errors in an hour',
  'spend-80': '80% of the AI limit used',
  'spend-100': 'AI limit reached',
  disk: 'Disk nearly full',
}

/** Health (PRD O5): the Daily search, stuck searches, errors, slow pages, disk and alerts. */
function Health() {
  const { tick, toast } = useApp()
  const [status, setStatus] = useState<Status | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const load = () =>
      api.status().then(
        (s) => {
          setStatus(s)
          setFailed(null)
        },
        (err) => setFailed(errorText(err)),
      )
    load()
    const t = window.setInterval(load, 60000)
    return () => window.clearInterval(t)
  }, [tick])
  const checkNow = async () => {
    try {
      await api.healthCheckNow()
      toast('Checking now. Any alert that is due is emailed to you.')
    } catch (err) {
      toast({ text: errorText(err), bad: true })
    }
  }
  const row = (ok: boolean | null, name: string, detail: string, extra?: ReactNode) => (
    <div className="hrow">
      <i className={ok === null ? '' : ok ? 'ok' : 'bad'} />
      <b>{name}</b>
      <span className="hd muted">{detail}</span>
      {extra ?? <span />}
    </div>
  )
  if (!status) {
    return (
      <div className="sec">
        <div className="sec-l">
          <h2>Health</h2>
        </div>
        <div className="sec-r">
          <p className={failed ? 'error-text' : 'muted'}>{failed ?? 'Checking…'}</p>
        </div>
      </div>
    )
  }
  const d = status.daily
  const last = d.last
  const daily = !d.on
    ? 'Off'
    : d.missed
      ? `Today's ${d.at} search has not started.`
      : last
        ? `Last ${when(last.started_at)}: ${last.status === 'failed' ? `failed (${last.error ?? 'no reason'})` : last.status}`
        : `Set for ${d.at}. Not run yet.`
  const disk = status.disk
  return (
    <div className="sec">
      <div className="sec-l">
        <h2>Health</h2>
        <p>Problems from the last 24 hours. JAP checks every hour and emails you once a day about anything below that needs you.</p>
      </div>
      <div className="sec-r">
        <div className="health">
          {row(!d.on ? null : !d.missed && last?.status !== 'failed', 'Daily search', daily)}
          {row(
            status.stuck_runs.length === 0,
            'Stuck searches',
            status.stuck_runs.length === 0
              ? 'None'
              : status.stuck_runs.map((r) => `${STARTED_BY[r.trigger as Run['trigger']] ?? r.trigger}, started ${when(r.started_at)}, still ${r.status}`).join('; '),
          )}
          {row(
            status.errors_24h === 0,
            'Errors',
            status.errors_24h === 0 ? 'None in the last 24 hours' : `${status.errors_24h} in the last 24 hours`,
            status.error_groups.length > 0 ? (
              <button type="button" className="btn-link" onClick={() => setOpen(!open)}>
                {open ? 'Hide' : 'Show'}
              </button>
            ) : undefined,
          )}
          {open && status.error_groups.length > 0 && (
            <div className="table-scroll">
              <table className="hist">
                <thead>
                  <tr>
                    <th>What</th>
                    <th>Times</th>
                    <th>Last</th>
                    <th>Latest message</th>
                  </tr>
                </thead>
                <tbody>
                  {status.error_groups.map((g) => (
                    <tr key={g.event}>
                      <td className="mono">{g.event}</td>
                      <td className="mono">{g.count}</td>
                      <td className="mono">{when(g.last_at)}</td>
                      <td title={g.request_id ? `Request ${g.request_id}` : undefined}>{g.last_message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {row(
            status.slow_routes.every((r) => r.avg_ms < 2000),
            'Slowest pages',
            status.slow_routes.length === 0
              ? 'No requests yet'
              : status.slow_routes
                  .slice(0, 3)
                  .map((r) => `${r.method} ${r.route.replace(/^\/api/, '')} ${r.avg_ms} ms on average (${r.count})`)
                  .join(' · '),
          )}
          {row(
            disk.used_pct <= 85,
            'Disk',
            disk.total_bytes
              ? `${disk.used_pct}% used, ${size(disk.free_bytes)} free. JAP's files: ${size(disk.data_bytes)}, of which backups ${size(disk.backups_bytes)}.`
              : 'Data folder not found',
          )}
          {row(
            status.alerts.length === 0,
            'Alerts sent',
            status.alerts.length === 0
              ? 'None in the last 7 days'
              : status.alerts.map((a) => `${ALERT_NAMES[a.key.split(':')[0]] ?? a.detail ?? a.key} (${when(a.sent_at)})`).join('; '),
            <button type="button" onClick={() => void checkNow()}>
              Check now
            </button>,
          )}
        </div>
      </div>
    </div>
  )
}

const ACCOUNT_STATE = { active: 'Active', unverified: 'Email not confirmed', off: 'Turned off' }

/** One account's limits, for the admin to change (S12). An empty box means the plan's number. */
function LimitsForm({ account, save, close }: { account: Account; save: (l: { ai_usd: number | null; boards: number | null; applications_per_day: number | null }) => Promise<void>; close: () => void }) {
  const p = account.plan
  const [ai, setAi] = useState(p.custom ? String(p.ai_usd) : '')
  const [boards, setBoards] = useState(p.custom && p.boards !== null ? String(p.boards) : '')
  const [sends, setSends] = useState(p.custom ? String(p.applications_per_day) : '')
  const num = (v: string) => (v.trim() === '' ? null : Number(v))
  const box = (label: string, value: string, set: (v: string) => void, max: number, step: number) => (
    <label className="inline">
      {label}
      <input type="number" min={0} max={max} step={step} style={{ width: 80 }} value={value} placeholder="plan" onChange={(e) => set(e.target.value)} />
    </label>
  )
  return (
    <form
      className="limits"
      onSubmit={(e) => {
        e.preventDefault()
        void save({ ai_usd: num(ai), boards: num(boards), applications_per_day: num(sends) }).then(close)
      }}
    >
      {box('Companies', boards, setBoards, 10000, 1)}
      {box('Applications a day', sends, setSends, 100, 1)}
      {box('AI a month ($)', ai, setAi, 1000, 0.5)}
      <button type="submit" className="btn-primary">
        Save
      </button>
      <button type="button" className="btn-link" onClick={close}>
        Cancel
      </button>
      <span className="muted small">Leave a box empty for the plan's own number.</span>
    </form>
  )
}

/** Accounts (PRD S12), for the admin: who has one, turning one off, pausing sign-up, and
 * what the AI has cost this month. Nothing a user wrote is shown here. */
function AccountsCard() {
  const { toast } = useApp()
  const [data, setData] = useState<Accounts | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  useEffect(() => {
    const stop = new AbortController()
    admin.accounts(stop.signal).then(setData, (err) => {
      if (!stop.signal.aborted) setFailed(errorText(err))
    })
    return () => stop.abort()
  }, [])
  const act = async (fn: () => Promise<Accounts>, done: string) => {
    try {
      setData(await fn())
      toast(done)
    } catch (err) {
      toast({ text: errorText(err), bad: true })
    }
  }
  return (
    <div className="sec">
      <div className="sec-l">
        <h2>Accounts</h2>
        <p>Everyone with an account on this JAP. Only you see this.</p>
      </div>
      <div className="sec-r">
        {failed && <span className="error-text small">{failed}</span>}
        {!data && !failed && <span className="muted">Loading…</span>}
        {data && (
          <>
            <label className="inline">
              <input
                type="checkbox"
                checked={!data.sign_up_paused}
                onChange={(e) => act(() => admin.pauseSignUp(!e.target.checked), e.target.checked ? 'Anyone can create an account again.' : 'New accounts are paused. Existing ones carry on.')}
              />
              Anyone can create an account
            </label>
            <span className="muted small">
              AI this month: <span className="mono">{money(data.spend_total.month_usd)}</span> of <span className="mono">{money(data.spend_total.cap_usd)}</span> in all, of which{' '}
              <span className="mono">{money(data.spend_shared.month_usd)}</span> of <span className="mono">{money(data.spend_shared.cap_usd)}</span> for reading job descriptions, which everyone shares.
            </span>
            <div className="health accounts">
              {data.accounts.map((a) => (
                <div key={a.id} className="hrow">
                  <i className={a.state === 'active' ? 'ok' : a.state === 'off' ? 'bad' : ''} />
                  <b>
                    {a.email}
                    {a.is_admin && <span className="muted"> · admin</span>}
                  </b>
                  <span className="hd muted">
                    {ACCOUNT_STATE[a.state]} · joined {when(a.joined_at)} · {a.last_sign_in_at ? `last signed in ${when(a.last_sign_in_at)}` : 'never signed in'} · AI{' '}
                    {money(a.spend.month_usd)} of {money(a.spend.cap_usd)} · {a.plan.custom ? 'Limits set by you' : `${a.plan.name} plan`}: {planWords(a.plan)}
                  </span>
                  <button type="button" className="btn-link" onClick={() => setEditing(editing === a.id ? null : a.id)}>
                    Limits
                  </button>
                  {editing === a.id && (
                    <LimitsForm account={a} close={() => setEditing(null)} save={(l) => act(() => admin.setLimits(a.id, l), `Limits saved for ${a.email}.`)} />
                  )}
                  {!a.is_admin &&
                    (a.state === 'off' ? (
                      <button type="button" className="btn-link" onClick={() => act(() => admin.turnOn(a.id), `${a.email} is on again.`)}>
                        Turn on
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn-link"
                        title="Signs the account out everywhere and stops its searches and applications. Its data is kept."
                        onClick={() => act(() => admin.turnOff(a.id), `${a.email} is turned off and signed out.`)}
                      >
                        Turn off
                      </button>
                    ))}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

/** Agreeing to JAP sending without showing you first (S14). Its own screen, because it is
 * a different thing to agree to than JAP applying at all. */
function AutoOptInModal({ onCancel, onAgree }: { onCancel: () => void; onAgree: () => Promise<void> }) {
  const [agreed, setAgreed] = useState(false)
  const [busy, setBusy] = useState(false)
  return (
    <div className="modal-bg" onClick={onCancel}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="auto-title" onClick={(e) => e.stopPropagation()}>
        <div>
          <h2 id="auto-title">Let JAP apply without asking</h2>
          <p style={{ marginTop: 10 }}>
            JAP will fill in forms and press Submit <b>in your name</b> without showing them to you first, for jobs it finds on its own as well as ones you pick. Only jobs with nothing unclear are sent; the rest
            still wait for you.
          </p>
          <p style={{ marginTop: 10 }}>You can turn this off again here at any time. Applications already sent cannot be unsent.</p>
          <label className="tick" style={{ marginTop: 12 }}>
            <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
            <span>I agree to JAP applying for jobs in my name, without asking me each time.</span>
          </label>
        </div>
        <div className="mfoot">
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!agreed || busy}
            onClick={() => {
              setBusy(true)
              void onAgree().finally(() => setBusy(false))
            }}
          >
            {busy ? 'One moment…' : 'Turn it on'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function SettingsPage() {
  const { profile, setProfile, toast } = useApp()
  const { user } = useAuth()
  const [askAuto, setAskAuto] = useState(false)
  const flip = async (s: Switches, done?: string) => {
    try {
      setProfile(await api.setSwitches(s))
      if (done) toast(done)
    } catch (err) {
      toast({ text: errorText(err), bad: true })
    }
  }
  if (!profile) {
    return (
      <section className="page">
        <p className="muted">Loading…</p>
      </section>
    )
  }
  const automatic = profile.apply_mode === 'automatic'
  const spend = profile.spend
  const askAutoModal = askAuto && (
    <AutoOptInModal
      onCancel={() => setAskAuto(false)}
      onAgree={async () => {
        setAskAuto(false)
        await flip({ submit_opt_in: true, auto_opt_in: true, apply_mode: 'automatic' }, 'JAP now sends applications with nothing unclear.')
      }}
    />
  )
  const pct = Math.min(100, spend.cap_usd ? (spend.month_usd / spend.cap_usd) * 100 : 100)
  return (
    <section className="page">
      {askAutoModal}
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>How and when JAP works for you, and how you sign in. Changes save straight away.</p>
        </div>
      </div>

      <div className="sec">
        <div className="sec-l">
          <h2>Applying</h2>
          <p>What JAP does with jobs you choose to apply to, and with new jobs a search finds when it is asked to apply.</p>
        </div>
        <div className="sec-r">
          <div className="radio-list" role="radiogroup" aria-label="Applying">
            <label>
              <input type="radio" name="mode" checked={!automatic} onChange={() => void flip({ apply_mode: 'review' }, 'JAP prepares each application; you send it.')} />
              <span>
                <b>JAP prepares, I send</b>
              </span>
              <span className="d">JAP fills in each form. Nothing is sent until you press Send application.</span>
            </label>
            <label>
              <input
                type="radio"
                name="mode"
                checked={automatic}
                onChange={() => {
                  // Sending without showing you first is its own agreement (S14).
                  if (!profile.auto_opt_in) setAskAuto(true)
                  else void flip({ apply_mode: 'automatic' }, 'JAP now sends applications with nothing unclear.')
                }}
              />
              <span>
                <b>JAP applies for me</b>
              </span>
              <span className="d">Forms with nothing unclear are sent, at least 30 seconds apart. Jobs with questions, or no work mode stated, wait for you.</span>
              {automatic && (
                <span className="more">
                  <span className="inline">
                    <label htmlFor="cap">Send at most</label>
                    <Typed
                      id="cap"
                      type="number"
                      min={0}
                      max={profile.plan.applications_per_day}
                      style={{ width: 80 }}
                      saved={String(profile.daily_cap)}
                      commit={async (v) => {
                        const n = Number(v)
                        if (Number.isInteger(n) && n >= 0 && n <= profile.plan.applications_per_day && n !== profile.daily_cap) await flip({ daily_cap: n }, `At most ${n} a day.`)
                      }}
                    >
                      <span>applications a day (your plan allows {profile.plan.applications_per_day})</span>
                    </Typed>
                  </span>
                  <span className="inline">
                    <label htmlFor="skills-bar">Only jobs with at least</label>
                    <Typed
                      id="skills-bar"
                      type="number"
                      min={0}
                      max={100}
                      step={5}
                      style={{ width: 80 }}
                      saved={String(profile.auto_apply_min_skills)}
                      commit={async (v) => {
                        const n = Number(v)
                        if (v !== '' && Number.isInteger(n) && n >= 0 && n <= 100 && n !== profile.auto_apply_min_skills)
                          await flip({ auto_apply_min_skills: n }, n ? `JAP sends only jobs with at least ${n}% of your required skills.` : 'JAP sends jobs whatever their skills match.')
                      }}
                    >
                      <span>% of my required skills</span>
                    </Typed>
                  </span>
                  <span className="d" style={{ gridColumn: 1 }}>
                    {profile.must_have_skills.length === 0
                      ? 'You have no required skills in Job preferences, so this is not used yet.'
                      : profile.auto_apply_min_skills === 0
                        ? 'Off: JAP sends jobs whatever their skills match.'
                        : 'Jobs below this, or with no skills stated, stay on your list for you to apply to yourself.'}
                  </span>
                </span>
              )}
            </label>
          </div>
          {/* What you have agreed JAP may do in your name (S14), and how to take it back. */}
          <p className="help">
            {profile.submit_opt_in ? (
              <>
                You have agreed that JAP may apply for jobs in your name
                {profile.auto_opt_in ? ', without asking you each time' : ''}.{' '}
                <button
                  type="button"
                  className="btn-link"
                  onClick={() => void flip({ submit_opt_in: false }, 'JAP will not apply for you. Nothing more will be sent.')}
                >
                  Take that back
                </button>
                , and nothing more is sent.
              </>
            ) : (
              'You have not agreed to JAP applying in your name yet, so nothing can be sent. The first time you press Apply it asks.'
            )}
          </p>
          <div className="inline spread">
            <label className="inline" htmlFor="daily-at">
              Search for new jobs every day at
              <Typed
                id="daily-at"
                type="time"
                style={{ width: 'auto' }}
                saved={hhmm(profile.daily_run_at)}
                commit={async (v) => {
                  if (v) await flip({ daily_run_at: v }, `Daily search at ${v}.`)
                }}
              />
            </label>
            <input
              type="checkbox"
              role="switch"
              className="sw"
              aria-label="Daily search"
              checked={profile.daily_run}
              onChange={(e) => void flip({ daily_run: e.target.checked }, e.target.checked ? `Daily search is on, at ${hhmm(profile.daily_run_at)}.` : 'Daily search is off.')}
            />
          </div>
          <p className="help">
            {profile.daily_run
              ? `Every day at ${hhmm(profile.daily_run_at)}, JAP searches every company you follow${automatic ? ' and sends what it can' : ''}. It runs only while this computer is awake.`
              : 'Off: JAP searches only when you press Find new jobs, save Job preferences or use a new resume.'}
          </p>
        </div>
      </div>

      <SearchHistory />

      <div className="sec">
        <div className="sec-l">
          <h2>Your plan</h2>
          <p>What this account can use. An AI model reads your resume and writes your answers and cover letters; that is the AI counted here.</p>
        </div>
        <div className="sec-r">
          <span>
            <b>{profile.plan.custom ? 'Your limits' : `${profile.plan.name} plan`}:</b> {planWords(profile.plan)}.
          </span>
          <div className="inline spread">
            <span>
              <span className="mono">{money(spend.month_usd)}</span> of <span className="mono">{money(spend.cap_usd)}</span> used this month
            </span>
            <label className="inline">
              Monthly limit ($)
              <Typed
                type="number"
                min={0}
                max={100}
                step={0.5}
                style={{ width: 90 }}
                saved={spend.cap_usd.toFixed(2)}
                commit={async (v) => {
                  const n = Number(v)
                  if (v.trim() !== '' && Number.isFinite(n) && n >= 0 && n <= 100 && n !== spend.cap_usd) await flip({ spend_cap_usd: n }, `Monthly limit set to ${money(n)}.`)
                }}
              />
            </label>
          </div>
          <div className={spend.reached ? 'meter full' : 'meter'} role="meter" aria-valuemin={0} aria-valuemax={spend.cap_usd} aria-valuenow={spend.month_usd} aria-label="AI spend this month">
            <span style={{ width: `${pct}%` }} />
          </div>
          <span className={spend.reached ? 'error-text small' : 'muted small'}>
            {spend.reached
              ? 'Limit reached: JAP has stopped writing answers and cover letters for you until next month. Finding jobs carries on.'
              : user.is_admin
                ? 'When the limit is reached, JAP stops writing answers and letters for you until next month, and a banner shows on Jobs.'
                : 'When the limit is reached, JAP stops writing answers and letters for you until next month, and a banner shows on Jobs. You can lower the limit, not raise it above your plan.'}
          </span>
        </div>
      </div>

      <AccountSection />

      {/* The service as a whole is the admin's (S12): nobody else can read or change it. */}
      {user.is_admin && (
        <>
          <AccountsCard />
          <SystemStatus />
          <Health />
        </>
      )}
    </section>
  )
}
