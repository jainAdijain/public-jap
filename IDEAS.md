# Ideas

The list of what would make public-jap better and is not being built yet. One serial order: an
`IDEA-N` is assigned when the idea is first recorded, never changes and is never reused, the
same way `JAP-N` works in `BUGS.md`. A defect goes in `BUGS.md`; something JAP does not do yet
goes here.

An idea lands here when Aditya says "move to idea" (or "move to ideas") about something raised
in a session. Being on this list is not agreement to build it: an idea becomes work only when
Aditya says so, and it is written into the PRD first.

Status is `Open`, `Agreed` (to build, not started), `Built (vX.Y.Z)` or `Dropped` with the
reason. "Phase" is the phase of the public-jap plan (single-user JAP to a public multi-user
service) the idea belongs to; Phase 1, each user's own data with open sign-up and per-user AI
limits, is built on the `tenancy` branch and is not listed.

| Id | Phase | Status | Idea |
| --- | --- | --- | --- |
| [IDEA-1](#idea-1--google-sign-in) | 2 | Built (v0.20.0) | Google sign-in |
| [IDEA-2](#idea-2--searches-side-by-side-one-per-user) | 2 | Built (v0.20.0) | Searches side by side, one per user |
| [IDEA-3](#idea-3--a-freshness-window-on-the-shared-board-fetch) | 2 | Built (v0.20.0) | A freshness window on the shared board fetch |
| [IDEA-4](#idea-4--the-daily-search-in-each-users-own-time-zone) | 2 | Built (v0.20.0) | The daily search in each user's own time zone |
| [IDEA-5](#idea-5--delete-my-account-and-export-my-data) | 2 | Built (v0.20.0) | Delete my account, and export my data |
| [IDEA-6](#idea-6--consent-before-jap-acts-for-a-user) | 2 | Built (v0.20.0) | Consent before JAP acts for a user |
| [IDEA-7](#idea-7--a-privacy-notice-at-sign-up) | 2 | Built (v0.20.0) | A privacy notice at sign-up |
| [IDEA-8](#idea-8--a-your-plan-line-in-settings) | 1 follow-up | Built (v0.19.0) | A "Your plan" line in Settings |
| [IDEA-9](#idea-9--invites) | - | Open | Invites |
| [IDEA-10](#idea-10--an-always-on-server-instead-of-the-mac) | 3 | Open | An always-on server instead of the Mac |
| [IDEA-11](#idea-11--encrypted-off-site-backups) | 3 | Open | Encrypted off-site backups |
| [IDEA-12](#idea-12--no-adminer-and-no-user-content-for-the-admin-on-the-public-stack) | 3 | Open | No Adminer, and no user content for the admin, on the public stack |
| [IDEA-13](#idea-13--request-hardening) | 3 | Open | Request hardening |
| [IDEA-14](#idea-14--a-retention-period-for-traces-and-screenshots) | 3 | Open | A retention period for traces and screenshots |
| [IDEA-15](#idea-15--applications-side-by-side-across-worker-machines) | 4 | Open | Applications side by side across worker machines |
| [IDEA-16](#idea-16--a-security-code-address-for-each-user) | 4 | Open | A security-code address for each user |
| [IDEA-17](#idea-17--pacing-for-submissions-across-all-users) | 4 | Open | Pacing for submissions across all users |
| [IDEA-18](#idea-18--an-outbound-address-for-each-user) | 4 | Open | An outbound address for each user |
| [IDEA-19](#idea-19--bring-your-own-openrouter-key) | 4 | Open | Bring your own OpenRouter key |
| [IDEA-20](#idea-20--a-decision-on-job-site-blocking-and-terms-of-use) | 4 | Open | A decision on job-site blocking and terms of use |
| [IDEA-21](#idea-21--paid-plans) | 5 | Open | Paid plans |
| [IDEA-22](#idea-22--a-browser-extension-that-submits-from-the-users-own-machine) | 5 | Open | A browser extension that submits from the user's own machine |
| [IDEA-23](#idea-23--the-public-launch-review) | 6 | Open | The public launch review |
| [IDEA-24](#idea-24--a-devin-cloud-blueprint-and-the-untested-setup-path) | - | Open | A Devin cloud blueprint, and the untested setup path |

---

## IDEA-1 — Google sign-in

**Status:** Built (v0.20.0). Recorded 17 Nov 2026, from the public-jap plan (Phase 2).

Sign in with Google next to email and password. OIDC authorization-code flow with PKCE over
`httpx`; a `user_identity (user_id, provider, subject)` table; `password_hash` becomes nullable.
A Google account is linked to an existing one only when Google reports the email as verified.

## IDEA-2 — Searches side by side, one per user

**Status:** Built (v0.20.0). Recorded 17 Nov 2026, from the public-jap plan (Phase 2).

After Phase 1 searches still go through one queue for the whole service, so one user's search
waits behind everyone else's. `one_queued_run` and `start_run` become per user, and
`run-pipeline` gets a concurrency of 1 per `user_id` under a global ceiling.

## IDEA-3 — A freshness window on the shared board fetch

**Status:** Built (v0.20.0). Recorded 17 Nov 2026, from the public-jap plan (Phase 2).

A board is fetched once however many users follow it. `fetch-board` is keyed per board with a
freshness window, so two users searching minutes apart share one fetch instead of making two.

## IDEA-4 — The daily search in each user's own time zone

**Status:** Built (v0.20.0). Recorded 17 Nov 2026, from the public-jap plan (Phase 2).

`daily-schedule` runs per user at their morning, from `profile.tz`, not at one hour for all.

## IDEA-5 — Delete my account, and export my data

**Status:** Built (v0.20.0). Recorded 17 Nov 2026, from the public-jap plan (Phase 2).

Delete-my-account removes every private row and file; export gives a zip of them.
`application_frozen` needs an erasure path of its own.

## IDEA-6 — Consent before JAP acts for a user

**Status:** Built (v0.20.0). Recorded 17 Nov 2026, from the public-jap plan (Phase 2).

Acceptance of the terms and the privacy notice at sign-up, an explicit opt-in before public-jap
submits an application in a user's name, and a second one before automatic mode.

## IDEA-7 — A privacy notice at sign-up

**Status:** Built (v0.20.0), with the notice itself still a marked draft for Aditya to
write. Recorded 17 Nov 2026, from the Phase 1 session.

Once strangers sign up JAP holds their name, email, phone, resume and answers, and India's DPDP
Act and GDPR expect them to be told what is collected and why before they hand it over. A short
page saying what is stored, that it is used only to find jobs and apply for them, that text goes
to an AI provider with contact details removed, that JAP submits in their name, and how to get
the data deleted; plus an "I agree" tick on sign-up. The wording is a legal statement in
Aditya's name, so he reads it before it goes live. Nothing in Phase 1 shows such a page.

## IDEA-8 — A "Your plan" line in Settings

**Status:** Built (v0.19.0). Recorded 17 Nov 2026, from the Phase 1 session.

A user sees no plan anywhere, and the free limit is 1 company, so nearly every new user meets it
as an unexplained error. One line in Settings, "Free plan: 1 company, 20 applications a day, $2
of AI a month", and three fixes with it:

- show the company limit next to "Companies to follow" before it is hit;
- the error reads "You can follow 1 companies" when the limit is 1;
- the daily cap field accepts 50 and shows 50 while only 20 a day are sent: cap the field for
  non-admin users.

Files: `frontend/src/pages/SettingsPage.tsx`, `backend/api/boards.py`.

## IDEA-9 — Invites

**Status:** Open. Recorded 17 Nov 2026, from the Phase 1 session.

Sign-up by invitation only, to control exactly who gets in. Left out for now because the admin
can already pause sign-up and turn an account off; worth building if strangers start finding
the address.

## IDEA-10 — An always-on server instead of the Mac

**Status:** Open; the host is recommended, not confirmed. Recorded 17 Nov 2026, from the
public-jap plan (Phase 3).

Live is served from a Mac through the Cloudflare Tunnel, so when the Mac sleeps the site is down
for everyone and nobody's daily search runs. One Linux VPS running `infra/compose.release.yaml`
behind Cloudflare; the same image run as `api` and as `worker` (serves `/api/inngest`, runs
Playwright) so workers can be added; Ollama in a CPU container with `nomic-embed-text`;
monitoring. The path to scale after that is configuration: managed Postgres, more workers, S3
for files, Inngest Cloud.

## IDEA-11 — Encrypted off-site backups

**Status:** Open. Recorded 17 Nov 2026, from the public-jap plan (Phase 3).

`pg_dump` encrypted to object storage on a documented schedule, and full-disk encryption on the
server. Resume text and answers stay plaintext in the database because trigram matching needs
them.

## IDEA-12 — No Adminer, and no user content for the admin, on the public stack

**Status:** Open. Recorded 17 Nov 2026, from the public-jap plan (Phase 3).

Adminer is not part of the public stack, and the admin screens show counts and health only,
never a user's resume, answers or jobs.

## IDEA-13 — Request hardening

**Status:** Open. Recorded 17 Nov 2026, from the public-jap plan.

Unsafe methods require a matching `Origin` or `Sec-Fetch-Site`, and board detection, which
fetches an address a user typed, is checked for server-side request forgery.

## IDEA-14 — A retention period for traces and screenshots

**Status:** Open; 30 days is proposed, not agreed. Recorded 17 Nov 2026, from the public-jap
plan.

Traces and screenshots are deleted after a fixed period instead of being kept for ever.

## IDEA-15 — Applications side by side across worker machines

**Status:** Open. Recorded 17 Nov 2026, from the public-jap plan (Phase 4).

Applications go one at a time for the whole service. One browser per user, a maximum per worker
machine, several workers. Capacity is browsers times applications per browser-hour, and the time
one application takes is to be measured, not assumed: 2 minutes was a guess, under which 150
users sending 20 each need about 10 browsers for 10 hours.

## IDEA-16 — A security-code address for each user

**Status:** Open. Recorded 17 Nov 2026, from the public-jap plan (Phase 4).

Greenhouse emails a code to the applicant, and `security_code.py` takes the newest such mail
after Submit, which with several users waiting can hand one user another's code. Each user gets
a private forwarding address and sets a mail filter once; a code is matched by the address it
was sent to and by arriving after that user's Submit. Typing the code in the UI stays as the
fallback. To test first: which header carries the recipient after a forward depends on the mail
provider; an inbound-email service that reports the recipient is the alternative.

## IDEA-17 — Pacing for submissions across all users

**Status:** Open. Recorded 17 Nov 2026, from the public-jap plan (Phase 4).

On top of each user's daily cap and 30-second gap: a throttle per ATS, a global daily ceiling,
applications spread across the day, and a per-user kill switch next to the global
`JAP_SUBMIT_ENABLED`. CAPTCHA, security-code and failure rates per site are watched, since they
say when IDEA-18 is needed.

## IDEA-18 — An outbound address for each user

**Status:** Open. Recorded 17 Nov 2026, from the public-jap plan (Phase 4).

Every application leaves from the server's one IP, so many names from one address look like a
bot. Playwright can use a different proxy per browser, so each user can be given a stable
outbound address. It keeps automatic mode, costs money per user, and some sites block known
proxy ranges. Build the option in early so switching it on is a setting.

## IDEA-19 — Bring your own OpenRouter key

**Status:** Open. Recorded 17 Nov 2026, from the public-jap plan (Phase 4).

A user's own key pays for their personal AI work instead of the free quota. A `user_secret`
table holding AES-GCM ciphertext under a master key from the environment (adds `cryptography`);
`llm_call.billed_to` already has `byok` in mind.

## IDEA-20 — A decision on job-site blocking and terms of use

**Status:** Open; Aditya's call, needed before Phase 4 ships. Recorded 17 Nov 2026, from the
public-jap plan.

Server-side submission for many users risks being blocked and may breach the job sites' terms on
automated submission. Code reduces the first and does not settle the second. Whether the sites'
official submit APIs are open to anyone but the employer was not verified.

## IDEA-21 — Paid plans

**Status:** Open. Recorded 17 Nov 2026, from the public-jap plan (Phase 5).

Free is the only plan and `limits_for(user)` already returns its limits (AI quota, companies
followed, applications a day), so a paid plan is more rows behind that function plus a plan page
and payment. Today's amounts are caps on the operator's OpenRouter bill, not prices.

## IDEA-22 — A browser extension that submits from the user's own machine

**Status:** Open; only if the blocking numbers call for it. Recorded 17 Nov 2026, from the
public-jap plan (Phase 5).

The form is filled and submitted in the user's own browser from their own IP, so it looks like
the person it is. Nothing happens while their laptop is closed, so automatic mode is lost.

## IDEA-23 — The public launch review

**Status:** Open. Recorded 17 Nov 2026, from the public-jap plan (Phase 6).

Before announcing: a privacy policy and terms with a view on the DPDP Act and GDPR, a load test,
and a security review.

## IDEA-24 — A Devin cloud blueprint, and the untested setup path

**Status:** Open. Recorded 17 Nov 2026, from the multiple-harness session.

A Devin cloud blueprint that runs `infra/scripts/agent-setup.sh`, so a cloud session starts with
the checks working. The apt path in that script, for a sandbox without Docker, has not run
anywhere; the Docker path and the `JAP_HOST_DATABASE_URL` path have.
