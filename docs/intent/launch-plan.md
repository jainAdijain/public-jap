# Launch plan

The steps of Phase 6 ([plan.md](plan.md)), from today (v0.24.0 on `main`) to open
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

- [x] CI green on `launch` (`cryptography` 50, Trivy pinned to a commit, the image patched and
      without pip)
- [x] **Owner:** fill in the four operator facts in `frontend/src/legal.ts`: name, postal
      address, contact address, governing law. Aditya, Rajasthan, India,
      privacy@tutorial.monster (also in `security.txt`), India. The draft banner is gone
- [x] **Owner:** make privacy@tutorial.monster receive mail: an alias on the domain's
      existing mailbox (not Cloudflare Email Routing, which would replace its MX records).
      A test message arrived on 26 Nov 2026
- [ ] **Owner:** replace "Rajasthan, India" with a full postal address before Plus goes on
      sale; Razorpay checks for one on the site
- [ ] **Owner:** read the terms and privacy notice as a statement in your own name. The code
      half is pinned by `test_privacy_claims.py`; the wording is yours. Have someone you trust
      read it too (ideally someone who knows DPDP/GDPR)
- [ ] Raise `TERMS_VERSION` **once**, with the final wording, so early accounts are asked again
      at launch and not twice in a week
- [x] Merge PR 6; update [state.md](../state.md) and the behaviour pages it
      changes in the same or the next PR

## Stage 2: the decisions that shape who can sign up

These do not block deploying, but they block letting strangers in.

Decided by Aditya on 26 Nov 2026 and built in v0.25.0 ([decisions.md](decisions.md), 27 to 30).

- [x] **D1: the AI money.** No free AI. `JAP_MONTHLY_COST_CAP_USD` is 0, and neither plan
      includes AI on JAP's key. An account brings its own OpenRouter key, or the admin grants
      that one account credit (Settings > Accounts, "AI credit a month"). With neither, the model
      is never called and the person is told to add a key or ask. The worst-case bill is the
      $5 service budget plus the grants. Using a person's own Claude or ChatGPT subscription is
      IDEA-25, not built.
- [x] **D2: Automatic mode.** Review mode for everyone. The admin allows Automatic account by
      account (Settings > Accounts, "Allow Automatic"), and decides about the public from the
      per-site refusal rates.
- [x] **D3: selling Plus.** Not for the soft launch, and not before real users have tried JAP:
      `JAP_BILLING_ENABLED=false` stays. When it comes back, Plus sells companies and
      applications only (its $8 of AI was removed). Before turning it on: a full postal
      address in the terms, a refund and cancellation page, the tax position (GST, a CA's
      question), a Razorpay live account, and one real test-mode checkout and webhook end to end.
      Meanwhile the admin raises one account's limits by hand when a tester needs more.
- [x] **D4: how the soft launch admits people.** An invite list (Settings > Accounts). Sign-up
      stays paused, and only an address on the list can create an account, by password or by
      Google; the invite is used up when it is. Nothing is emailed: Aditya tells the person.
- [x] **The first week's limits.** Keep 1 company and 1 application a day per account, raised
      by hand where needed. Set `JAP_DAILY_APPLICATIONS_TOTAL=50` in `.env.live` at the first
      deploy (stage 3), not 200.

## Stage 3: first real release on the server

Nothing below has run before: no `v*` tag has ever been pushed. Do it in this order so a
failure is cheap.

- [ ] Fix JAP-2 (a spent OpenRouter key retried until it floods the log): with one shared key
      and many accounts it becomes every account's problem. Close JAP-3 if it is obsolete
- [x] Build the images for arm64 too and give live its own Docker project and ports, so it
      can run on the ARM VM beside the private JAP (v0.25.2, decision 31)
- [x] Tag `v0.25.2`, push it, watch the Release workflow push both images to GHCR (both
      architectures, 26 Nov 2026)
- [x] Rehearse the release on the VM as a non-live stack: images pulled, every service
      healthy, migrations at head, admin then pause, a stranger refused, an encrypted
      dump restored into a scratch database (v0.25.2; the missing Ollama tag fixed in v0.25.3)
- [ ] On the server or VM: `server-setup.sh` if not done, a tunnel of the server's own,
      `make env ENV=live`, then the secrets by hand ([operations.md](../structure/operations.md))
- [ ] Set `JAP_SECRET_KEY` (32 random bytes, base64) in `.env.live`: without it nobody can
      save their own OpenRouter key, and after D1 that is the only AI most accounts have
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
