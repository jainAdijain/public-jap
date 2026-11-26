# Launch plan

The steps of Phase 6 ([plan.md](plan.md)), from today (v0.23.0 on `main`, PR 6 open) to open
sign-up and `v1.0.0`. Tick a box in the same pull request that does the step, and add any step
you find was missing. **Owner** marks what only
Aditya can do (a decision, a legal statement, a credential, a payment account); everything
else an agent can do or prepare.

The gate between stages is the **Pause sign-up** switch in Settings > Accounts.

## Stage 0: one source of truth

- [x] `docs/` with goal, state, architecture, behaviour, operations, decisions and this plan (PR 7)
- [x] `docs/` split into intent, structure and behaviour, with requirement ids, a phase plan, a
      traceability map and a drift check in CI (this restructure, decision 26)
- [ ] **Owner:** move the private product documents into `docs/intent/sources/` on the Mac
      (`mkdir -p docs/intent/sources && mv docs/private/* docs/intent/sources/`, or
      `mv docs/*.pdf docs/text docs/intent/sources/` if they still sit at the top of `docs/`).
      They stay ignored wherever they are; this keeps them beside the intent they feed.
- [ ] Build OPS-5, versions from git tags, before the first tag is pushed in stage 3, so the
      first release is already named the new way

## Stage 1: land PR 6 (v0.24.0)

- [ ] CI green on `launch` (it was re-running after the `cryptography` 48.0.1 upgrade and the
      Trivy pin)
- [ ] **Owner:** fill in the four operator facts in `frontend/src/legal.ts`: name, postal
      address, contact address, governing law. The draft banner goes away only then, and
      `make test` then insists `security.txt` carries the real contact
- [ ] **Owner:** read the terms and privacy notice as a statement in your own name. The code
      half is pinned by `test_privacy_claims.py`; the wording is yours. Have someone you trust
      read it too (ideally someone who knows DPDP/GDPR)
- [ ] Raise `TERMS_VERSION` **once**, with the final wording, so early accounts are asked again
      at launch and not twice in a week
- [ ] Merge PR 6; update [state.md](../state.md) and the behaviour pages it
      changes in the same or the next PR

## Stage 2: the decisions that shape who can sign up

These do not block deploying, but they block letting strangers in.

- [ ] **Owner, D1: the AI money.** At today's settings 150 free accounts can spend about $155 a
      month against a $20 total cap: nobody is slow, everybody stops. Pick one:
      raise `JAP_TOTAL_COST_CAP_USD` and pay; lower `JAP_MONTHLY_COST_CAP_USD` (an application
      costs 2 to 3 cents, so $0.50 still covers one a day); hold the number of accounts where
      the cap fits (about 15 today); or make Plus the way past it. *Suggested for the soft
      launch:* hold at ~15 accounts with the current caps, decide the rest from real spend.
- [ ] **Owner, D2: Automatic mode for the public** (IDEA-20). *Suggested:* Review mode only
      for the soft launch (hide or refuse Automatic for non-admin accounts), decide from the
      per-site refusal rates. If chosen, that is a small code change with a test.
- [ ] **Owner, D3: selling Plus.** *Suggested:* keep `JAP_BILLING_ENABLED=false` for the soft
      launch. Before turning it on: a refund and cancellation page, the tax position, a
      Razorpay live account, one real sandbox checkout and webhook end to end.
- [ ] **Owner, D4: how the soft launch admits people.** Pausing sign-up stops everyone, so
      letting in ten named people means un-pausing briefly or building invites (IDEA-9).
      *Suggested:* un-pause for a short window and send the address only to them; build
      invites only if strangers start arriving.
- [ ] **Owner:** the first week's limits (per-account caps, `JAP_DAILY_APPLICATIONS_TOTAL`).

## Stage 3: first real release on the server

Nothing below has run before: no `v*` tag has ever been pushed. Do it in this order so a
failure is cheap.

- [ ] Fix JAP-2 (a spent OpenRouter key retried until it floods the log): with one shared key
      and many accounts it becomes every account's problem. Close JAP-3 if it is obsolete
- [ ] Tag `v0.24.0`, push it, watch the Release workflow push both images to GHCR
- [ ] On the server or VM: `server-setup.sh` if not done, a tunnel of the server's own,
      `make env ENV=live`, then the secrets by hand ([operations.md](../structure/operations.md))
- [ ] Set `JAP_ADMIN_EMAIL` **before** the first start, so the first account (the admin) can
      only be yours; then create it
- [ ] `make deploy ENV=live TAG=v0.24.0`; `/api/health` green on all four checks;
      `make ollama-check` (embedding on CPU) and `make llm-check`
- [ ] `JAP_BACKUP_AGE_RECIPIENT` set; Settings > Status says encryption is on; one encrypted
      dump **restored** into a scratch database and checked; `make backup-pull` works from the Mac
- [ ] `make email-test` from live, and check SPF/DKIM so verify and reset emails do not land
      in spam
- [ ] Only now add the public hostname in Cloudflare, with a rate-limit rule on `/api/auth/*`
- [ ] Open the site in a browser with devtools: the CSP does not block anything on sign-in,
      sign-up (Turnstile) and the main screens
- [ ] Uptime check from outside on `/api/health` every 5 minutes, matching `"ok":true`
- [ ] Optional: `SENTRY_DSN`; Google sign-in with a real OAuth client, tried once

## Stage 4: use it yourself (sign-up paused)

- [ ] Two accounts of your own following the **same** company: each sees only its own list,
      hiding or applying in one changes nothing in the other, an id from one answers 404 in
      the other, export of one contains nothing of the other
- [ ] A real application end to end in Review mode, including the Applied email
- [ ] A Greenhouse security code through the per-account forwarding address
- [ ] Download my data and Delete my account on a throwaway account
- [ ] Look at the Phase 4 screens and narrow widths (JAP-7)
- [ ] `make capacity` on the real applications; compare with the load test's 120-second guess

## Stage 5: soft launch (a reviewer, then about ten people)

- [ ] One trusted second pair of eyes: security and wording review
- [ ] About ten people who will tell you when something is wrong
- [ ] Watch daily: `make errors`, Settings > Health, AI spend against the caps, per-site
      refused / asked-for-a-code rates, failed applications
- [ ] **Stop and pause sign-up** if applications fail repeatedly, spend tracks above the cap,
      a job site starts refusing, or the same error repeats
- [ ] Exit when: a week with no repeating error, no isolation surprise, spend inside budget,
      and people actually got applications out

## Stage 6: open sign-up

- [ ] D1 to D4 settled for the open public and recorded in [decisions.md](decisions.md)
- [ ] Tag `v1.0.0` (decision 25: 1.0.0 is the first public launch) and deploy it
- [ ] Un-pause sign-up
- [ ] Weekly: restore a backup now and then, `make backup-pull`, read Health and Insights,
      check Dependabot and the security scans, check per-site rates
- [ ] Monthly: AI spend against the OpenRouter bill; capacity against the server
- [ ] Bring the PRD up to date with the drift listed in [state.md](../state.md)

## Later, only when the numbers ask

IDEA-18 (an outbound address per user) if per-site refusals rise; IDEA-22 (browser
extension) if blocking makes server-side submission unworkable; a second merchant of record
for outside India; more workers (a replica count) when browser-hours run short.
