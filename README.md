# JAP: Job Application Pipeline

**Version 0.21.0 (live runs on a server, not the Mac), 20 Nov 2026.**

JAP watches the public job boards of the companies you pick (Greenhouse, Lever and Ashby),
lists the jobs that match your profile, and prepares or submits applications for you to review.
Anyone can create an account, and each account has its own private workspace: profile,
resume, companies, answers, searches and applications (see
[Sign-in and accounts](#sign-in-and-accounts)).
The [JAP PRD](https://claude.ai/artifact/WL9RYjsPDCTmknMLRqzKRj) is the single source of truth;
the [build plan](https://claude.ai/artifact/1TrqC2jt8SuPVbULa8i6eU) and the
[engineering design doc](https://claude.ai/artifact/XsWXaEvBdEsFf97GguqtAy) follow it.

## What runs

| Service | URL (127.0.0.1 only) | What it is |
| --- | --- | --- |
| ui | http://localhost:5173 | React + Vite UI: Jobs, Profile, Job preferences and Settings |
| app | http://localhost:8001 | FastAPI JSON API (`/api/health`), the half a browser talks to |
| worker | not published | The same image, running the Inngest functions and the Playwright browser (`/api/inngest`) |
| inngest | http://localhost:8290 | Inngest dev server, runs every pipeline step |
| db | localhost:5432 | Postgres 16 with pgvector, pg_trgm, pgcrypto |
| adminer | http://localhost:8080 | Database browser (server `db`, user/password/db `jap`) |

`app` and `worker` are one image started twice, `JAP_ROLE` apart. The API process does not
mount `/api/inngest` at all, so the endpoint that runs JAP's functions is not even present in
the process reachable from a browser; the worker, which does mount it, is published nowhere.
It also means the browser and the heavy work (Chromium, resume parsing) cannot starve each
other, and that live can add workers later by raising a replica count.

Each host port can be changed in `.env` (`JAP_UI_PORT`, `JAP_API_PORT`, `JAP_INNGEST_PORT`,
`JAP_DB_PORT`, `JAP_ADMINER_PORT`). The API and Inngest use 8001 and 8290 so they don't clash with
other projects on 8000 and 8288-8289 (an Inngest dev server publishes both). Inside Docker the services keep their usual ports.

Ollama is **not** in Docker locally. It runs natively on the Mac so it can use the GPU, and
the app reaches it at `http://host.docker.internal:11434`. On the server, which has no GPU
to use, it is a container in the stack at `http://ollama:11434`.

## The screens

The UI follows the [redesign mockup v0.5](https://claude.ai/artifact/5V2SRvBgWZu5stN5KY4H7L)
(PRD 1.5, U1 to U20): a sidebar with five sections, which becomes a top bar on a phone.

- **Jobs**: the last search line, **Find new jobs**, a status strip (All open, New, Needs you,
  Ready to send, Sent, Failed), a toolbar with search and filters, and the jobs table. Tick
  rows to **Apply** or **Hide** several at once; click a row to open the job panel, whose
  actions and content follow the job's status. `/` jumps to the search box (its tooltip says so).
- **Profile**: what is sent to employers. Resume and its versions, contact details and links,
  Saved answers, and the cover letter style.
- **Job preferences**: what decides which jobs show. Job titles, locations, companies to
  follow, jobs to hide, and Matching; and your required and nice-to-have skills, which never
  hide a job but give each one a skills match.
- **Insights**: jobs found and sent per week, results by job site, and AI spend (PRD 1.19).
- **Settings**: applying (JAP prepares, I send; or JAP applies for me, with a daily limit
  and a required-skills bar) and what you have agreed JAP may do in your name, the daily
  search and the time zone it runs in, Search history, AI usage, Your plan, Account
  (password, two-step sign-in, Google, the privacy notice, Download my data, Delete my
  account), System status (backup and form check) and Health (Daily search, stuck
  searches, errors, slow pages, disk, alerts sent).

Older names in this README map to the screen as follows: Not applied is **New**, Needs input
is **Needs you**, Ready for review is **Ready to send**, Approve and submit is **Send
application**, Applied is **Sent**, Skip is **Hide**, Retry is **Try again**, the answer bank
is **Saved answers**, a run is a **search**, the spend cap is the **AI limit**, My profile is
split into Profile and Job preferences, and the Runs and Status cards are in Settings.

## First run (macOS)

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and [Ollama](https://ollama.com/download).
2. Pull the embedder: `ollama pull nomic-embed-text`
3. `make start` creates `.env` from `.env.example` the first time. Put your OpenRouter key and
   email sender in `.env` (it is gitignored; never commit it). See [Sending email](#sending-email).
4. Open http://localhost:5173 and create your account. The first account is the admin
   (see [Sign-in and accounts](#sign-in-and-accounts)). Settings > System status should show
   API, Database, Inngest and Ollama as OK.

## Sign-in and accounts

Every page and every `/api` route is behind sign-in (PRD 1.18, S1 to S9); only `/api/health`
and `/api/auth/*` answer without a session.

**Each account has its own data (PRD 1.18 step 2: S7, S10, S12, S13).** Facts about jobs
are shared and everything about a person is private.

| Shared, fetched and read once | Private, one per account |
| --- | --- |
| companies and their boards, postings, listings (the facts: title, locations, skills, work mode), duplicates | profile and job preferences, resumes, answer bank, which companies you follow (`board_follow`), what each job is to you (`user_listing`: matched, skills match, status), searches, applications and their files, AI spend |

So when two people follow the same company it is fetched once and each description is read
by the AI once; each person's own filter then makes their own list, from the companies they
follow only. Hiding a job, applying to it or unfollowing its company changes your list and
nobody else's.

The check is made twice. Every route and every background job works for one user, and an id
that belongs to someone else answers Not Found, the same as an id that does not exist. Then
Postgres repeats it: the app runs as a role that owns nothing (`jap_app`), every transaction
names its user (`app.user_id`), and row-level security on every private table returns only
that user's rows, so a query that forgot its filter returns nothing rather than someone
else's data. Only work on shared data (fetching, reading postings, dedup, the admin's
screens) runs as the service (`backend/database/tenant.py`, migration `0013_rls`).

- **Creating an account.** Anyone can, from the sign-in screen. The first account on a fresh
  JAP is the admin and goes straight in; set `JAP_ADMIN_EMAIL` in `.env` to allow only your
  address for it. Every account after that gets an emailed link (single use, 24 hours) and
  sees Check your email, with Send again, until it is opened. The answer is the same whether
  or not the address already has an account. A new account starts empty: the admin's
  companies are not copied to it.
- **Limits on sign-up.** Sign-up, forgot password and Send again are limited per visitor
  address and per email (`JAP_AUTH_TRIES_PER_HOUR_IP`, `JAP_AUTH_TRIES_PER_HOUR_EMAIL`); past
  the limit the answer is 429. Behind the Cloudflare Tunnel set
  `JAP_CLIENT_IP_HEADER=CF-Connecting-IP`, or every visitor shares the tunnel's address. Set
  `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` to add Cloudflare's "prove you're human"
  check to sign-up and forgot password.
- **The admin** (Settings > Accounts) sees each account's address, when it joined and last
  signed in, its state and its AI spend this month, never anything a user wrote. The admin can
  turn an account off (it is signed out everywhere and its searches and applications stop;
  its data is kept) or on again, and pause sign-up. System status, Health, backups, form
  tests and deciding duplicates are the admin's too; to anyone else those routes are Not Found.
- **The free plan** is what every account but the admin's gets: 1 company to follow
  (`JAP_FREE_BOARDS`), 1 application a day (`JAP_FREE_DAILY_APPLICATIONS`) and $1 of AI a
  month (`JAP_MONTHLY_COST_CAP_USD`; an application costs about 2 to 3 cents of AI, so one a
  day stays under it). Settings shows it as **Your plan**, and Job preferences says how many
  companies the plan follows. The admin can give any one account its own numbers for all
  three (Settings > Accounts > Limits; an empty box is the plan's number) and is not held to
  the plan themselves. A user can lower their own AI limit and daily cap, never raise them.
- **AI limits.** An account's allowance pays for work done for it: parsing its resume,
  drafting answers, writing cover letters. Reading shared job descriptions is billed to the
  service instead (`JAP_SYSTEM_COST_CAP_USD`), which users never see, and everything together
  stops at `JAP_TOTAL_COST_CAP_USD`. One person reaching their allowance stops only them. A
  description is read only when its title is in a target role of someone who follows that
  company.
- **Files** (resumes, cover letters, screenshots, traces) are written under
  `data/users/<user id>/` and served only through the row that owns them. Files from before
  this keep their old paths and are served the same way.
- **Data from before.** Everything that existed when this was applied belongs to the admin
  (migration `0011_tenancy`).
- **Passwords** are at least 12 characters and stored as salted scrypt hashes. Five wrong
  tries lock that account's sign-in for 15 minutes.
- **Forgot password** emails a single-use link, good for 30 minutes, from the mailbox in
  `.env` to the account's address. It links to `JAP_UI_URL`, so set that to the address
  you open JAP at. Without a mailbox, use `make set-password EMAIL=...`.
- **Sign in with Google** (PRD S16), beside the password, when `GOOGLE_CLIENT_ID`,
  `GOOGLE_CLIENT_SECRET` and `GOOGLE_REDIRECT_URL` are set; all three empty and the button
  is not shown and the routes answer Not Found, so a fresh clone needs nothing from Google.
  OpenID Connect with PKCE: JAP fetches the token itself over TLS and checks its issuer,
  audience, expiry and nonce. An address that already has an account is joined up **only
  when Google says it is verified** — otherwise sign in with the password and connect from
  Settings > Account, because anyone can make a Google account carrying an address they do
  not own. An account created through Google has no password and still has to agree to the
  notice before it can do anything. Disconnecting is refused while Google is the only way
  in; set a password first.
- **Two-step sign-in** (Settings > Account): scan the QR code with an authenticator app,
  enter one code, and save the 8 recovery codes. Lost both? `make two-step-off EMAIL=...`.
- **Sessions** live in an HttpOnly cookie tied to the hostname. They end after 7 days unused or
  30 days after sign-in. Set `JAP_COOKIE_SECURE=true` wherever JAP is served over HTTPS.
- **Internal endpoints.** `/api/inngest` answers only direct calls from inside Docker, never
  a request carrying proxy headers (`X-Forwarded-For`, `Forwarded`, `Cf-Connecting-Ip`), and
  a write that comes from another site's page is refused.

## What you agree to

JAP holds a person's name, address, phone, resume and the answers they give to employers,
sends job and resume text to a model with the contact details taken out, and presses Submit
on a form in their name. Each of those is said plainly and agreed to before it happens
(PRD S14). Three separate agreements, because they are three different things:

| What | Asked where | Without it |
| --- | --- | --- |
| The terms and the privacy notice | The tick on sign-up | No `/api` route but health and `/api/auth/*` answers: the app shows the agreement screen and nothing else |
| JAP may apply in my name | The first time you press Apply | Nothing is prepared and nothing is sent; Apply says so and a run picks nothing |
| JAP may do that without asking first | Turning on "JAP applies for me" in Settings | Automatic mode cannot be turned on |

- **The notice** is readable before you create an account (from the tick), from the
  agreement screen and from Settings > Account at any time. It says what JAP stores, what it
  is used for, what goes to an AI model, what applying in your name means, and who can see
  it. The wording is a draft and says so at the top.
- **The version is kept, not just the date.** `TERMS_VERSION` in `backend/auth/consent.py`
  is the date the notice last changed, and an account is agreed only while its stored
  version matches. Raising it asks everyone again, so it changes when what the notice *says*
  changes, not when its wording is tidied. Accounts that existed before the notice are
  asked once on their next visit: the migration backfills nothing, because a consent written
  by a migration is one nobody gave.
- **Taking it back.** Settings > Applying shows what you have agreed to, with Take that
  back. Taking back "JAP may apply for me" takes "without asking" with it and puts the
  account back in Review mode: the narrower permission cannot outlive the wider one. Nothing
  further is sent after that, including an application already approved and waiting — Submit
  reads the opt-in again at the last moment, and fails the attempt rather than sending it.
  Applications already sent cannot be unsent.

## Your data, back or gone

Both are in **Settings > Account** (PRD S15), and both work even for an account that has
*not* agreed to the current notice — otherwise refusing it would leave a person with no way
out, and the agreement screen offers them too.

- **Download my data** is a zip: `profile.json`, `resumes.json`, `answers.json`,
  `applications.json`, `searches.json`, `companies-followed.json`, `jobs.json`,
  `ai-spend.json`, every file those rows point at under `files/`, and a `README.txt` saying
  what each one is. One file per private table, so a table added later cannot be left out
  of it (a test walks `tenant.PRIVATE_TABLES` and fails otherwise). Jobs, companies and
  postings are shared facts about the job market, so only your side of them is in it.
- **Delete my account** asks for your password, your two-step code when that is on, and
  your own address typed out. Then it is gone at once: every private row, the files with
  it, no grace period and no undo. The address can sign up again afterwards and starts
  empty. The admin's account cannot be deleted, and the admin can delete anyone else's for
  a user who asks by email.
- Deleting an Applied application is normally refused outright (A2). Erasure is the single
  exception, through a flag set with `SET LOCAL` inside that one transaction (migration
  `0016_erasure`); every other statement still meets the rule.
- Two things outlive a deletion, and the notice says so: `llm_call` rows keep what each AI
  call cost with the `user_id` nulled and `billed_to` set to `deleted` (no name, no text —
  it is what the OpenRouter bill is counted from), and `log_event` lines keep the service's
  own record, also with the user nulled, already scrubbed and already deleted after 30 days.
- A company nobody follows once you are gone is switched off and its postings closed, the
  same as unfollowing it by hand.

## Personal data and models

No name, email, phone, address or profile link is sent to any model. `backend/llm/redact.py`
swaps them for placeholders such as `[NAME]`, a guard blocks any request that still contains
one, and `backend/llm/client.py` is the only code that talks to OpenRouter (a test enforces
it). Each call is logged in `llm_call` with its cost and whose allowance paid; model steps
stop once this month's spend reaches that limit (AI limits, above). Reading a shared job
posting uses no user's details at all: its text is redacted generically.

## Fetching jobs

Add companies under **Companies to follow** in Job preferences by pasting their Greenhouse,
Lever or Ashby board URL (or typing the name), then press **Find new jobs** on Jobs. Each board is one `fetch-board` run
in Inngest, throttled to one request a second per ATS. Job descriptions are read by the
extract model once per description: a re-run with unchanged postings costs nothing. The
first run on a big board (some list 700+ jobs) makes one model call per job, so start with a
few companies and watch **AI usage** in Settings.

**Stop following** a company (the × on its name) asks first and says how many jobs it hides.
It closes every open posting of that board with the reason `unfollowed` and closes the jobs
left with no open posting on a company you still follow; nothing is deleted. Jobs you applied
to, or started an application for, stay in the list, and their panel says you stopped
following the company. No job description of an unfollowed company is read by the AI, reads
already queued included, and a search that is running stores nothing for it.

**Following it again** switches the same board back on and fetches nothing until the next
search. That search reopens the jobs the board still lists, on the same rows: an unchanged
description reuses its stored reading, a changed one is read once, a job the employer took
down meanwhile stays Closed (its reason becomes `gone_from_board`), and a job you applied to
stays Sent. The search line counts reopened jobs apart from new ones.

Companies unfollowed before 0.17.0 left their postings open. `make close-unfollowed` lists
them and changes nothing; `make close-unfollowed APPLY=1` closes them. On live, run
`make backup ENV=live` first.

## One row per job

The same job is often posted on more than one board. After each run JAP groups postings into
listings: one row per company and role, with a badge for each board. Company names are
cleaned (Inc, Ltd, Technologies and the like are dropped, and aliases in `company_alias` map
to one name) and titles are reduced to a canonical role (Sr becomes Senior, locations and
work-mode words are dropped). Seniority always stays in the role, so Senior and Junior are
two rows. Postings join a listing when they share a requisition ID, have the same canonical
role, or are a near match (titles 90% alike and descriptions 92% alike). Pairs just below
that bar (titles 75%, descriptions 85%) are settled by the AI after each search's dedup: the
extract model reads both descriptions once and JAP merges them or keeps them apart, so you are
never asked. A pair it can't answer stays as two jobs until the next search. **Not the same
job** in the job panel splits a wrong join, and is remembered on later searches.

## Which jobs are listed

Jobs lists only the jobs that fit your Job preferences. The match rule is one or more rows
of Role and Location: a job is listed when it meets every condition in at least one
row. Job preferences offers two choices under **Matching**: job title or location; job
title and location (the default, in the database and in code).
A rule saved earlier that is none of these is kept and shown as Custom. Role means the job's cleaned title contains
one of your target roles (engineer and developer count alike); Location means a job location names one of yours, or the job is remote
and allows one of your Remote regions, or it is Remote - Worldwide, or you chose **All remote
jobs** (`remote_any`). Under Locations, office cities come with On-site and Hybrid ticks, and
remote jobs are one of three choices: none, only those open to your countries, or all. On top of that, the work
mode must be one you ticked (a job that doesn't say is listed with Work mode "Not stated"),
and excluded companies and title words are dropped.

The filter is one SQL query over the stored listings (`backend/pipeline/matching.py`), so
saving Job preferences updates the list in well under a second without fetching anything,
and the save bar shows how many jobs your unsaved changes would fit. Each row's "Fits" line shows
what it matched. The status strip counts each job once; the Job title filter counts each
job under the first job title it matches, or Other. The toolbar searches title, company and
location and filters by job title, location, work mode, posted date and what it fits on,
without changing your preferences. Every column sorts, and the table pages 10, 25 or 50 rows. Every run ends with the same filter.

### Skills match

Skills never hide a job (PRD 1.21, F5). Until 0.18.0 a third Matching choice listed a job
only when it named every one of your required skills, and a job whose description gave no
skills failed it, so good jobs went missing. Now the same SQL pass that filters also writes
a skills match on every listing (`required_hit`, `nice_hit`, `required_pct`), and Jobs
shows it in its own column:

- **Required**: how many of your required skills the job asks for, with the percentage and
  the missing ones, for example "4 of 6 required (67%)", "Missing aws, kafka".
- **Nice to have**: how many of your nice-to-have skills it asks for, "2 of 5 nice to have".
  With no required skills set, only this part shows.
- **Skills not stated**: no skills were found in the job description. The match is unknown,
  not 0, like Work mode "Not stated".

The Skills match column sorts (required %, then nice to have, unknown last), and the toolbar
filters by a minimum (25%, 50%, 75% or all of your required skills; unknown jobs are left
out while a minimum is set). Neither changes Job preferences. The job panel's **Why it fits**
lists the skills behind the numbers. The match is rewritten whenever you save your skills,
accept the skills from a resume, or JAP finishes reading a job description.

A match rule saved before 0.18.0 that used skills keeps its other conditions: a row of skills
alone is dropped, and a rule left empty becomes job title and location (migration
`0010_skills_match`, and the same when a rule is read).

`make demo` loads "JAP Demo Co" with six made-up postings on a Greenhouse and a Lever board,
so you can see grouping, a pair for the AI to settle, and Not the same job without waiting for real
boards. It also loads "Globex Demo" on an Ashby board with jobs for checking the filters:
Remote - Worldwide, Remote - US, Remote - APAC, an Intern title, an unclear work mode and a
job in Berlin. Runs skip demo boards; `make demo-clear` removes them.

## What starts a run

Four things start a run. A run is one account's: it fetches the companies that account
follows and applies with its settings.

**Searches go side by side, one per account** (PRD T6). Two people searching at the same
time both start; your own second search waits for your first. `JAP_MAX_PARALLEL_RUNS` (3)
is the ceiling on how many different accounts search at once, and the rest wait their turn.
A trigger that fires during your run queues one run behind it for you; more triggers join
that queued run instead of adding another (a role-scoped ask widens the queued run's scope).

A company is shared, so a board fetched for one account within `JAP_BOARD_FRESH_MINUTES`
(10) is not fetched again for the next: the second search reuses it and the run counts it
under `boards_fresh`. The reading is not skipped with it — who wants a job title is worked
out at fetch time, so an account that followed the company since, or that has just added a
target role, still gets its descriptions read.

- **Find new jobs** on Jobs: every company you follow. The arrow beside it searches for only
  some job titles and can also apply to the new jobs that fit (see Applying). The companies
  are still fetched whole, because their jobs are shared with anyone else who follows them;
  the titles narrow what the run applies for.
- **Daily search**: the switch in Settings, at 06:00 unless you change the time next to it,
  **in your own time zone** — the select beside the time (PRD U22). Server time zone means
  `JAP_TZ`, which is what every account used before it could say. An Inngest cron ticks
  every minute and fires once per day in the first five minutes after that time, so a run
  missed while the Mac sleeps is skipped, not caught up.
- **Job preferences saved**: the stored listings are re-filtered at once, then a fetch follows
  in the background; new matching jobs appear on Jobs without a reload. Saving only contact
  details or the cover letter style on Profile starts no search.
- **Resume update**: accepting the skills from a new resume re-filters and starts a run.

The line under the Jobs title shows the last search (when, what started it, and its counts)
or the one going now; **Search history** in Settings lists every search with its problems.
Its colour follows the search: teal with a pulsing dot while searching (at least 3 seconds;
the page checks every 2 seconds), green with **Show new** after a search that found new jobs
(until you next open Jobs), grey when nothing is new, orange when a company could not be read
or the AI limit was reached, and red with **Try again** when the search failed.

## Applying (Review mode)

Open a job and press **Apply** (or tick several rows and press Apply), or tick **Also apply to
the new jobs that fit** under the arrow beside Find new jobs to prepare every New job (up to your daily cap, 20 by default). The first
Apply asks you to agree to JAP applying in your name, once ([What you agree
to](#what-you-agree-to)). JAP freezes the job description,
opens the posting's public form in a headless Chromium (Playwright), lists every field and
whether it is required, and fills it:

- Contact fields (name, first and last name, email, phone, LinkedIn, GitHub, website,
  location) come from My profile, by code. No model ever sees or fills them.
- The resume field gets your active resume, under its original file name.
- Other questions are looked up in the answer bank (exact question, or 80% alike).
- A required free-text question with no saved answer gets an answer drafted for this job
  from the JD and your resume (the `answer` prompt, redacted like every model call). Drafts
  are not saved to the answer bank unless you save them.
- A question only you can answer (notice period, salary, start date, work authorisation,
  relocation and the like), a required choice with no saved answer, a file JAP doesn't have,
  or a CAPTCHA stops the job at **Needs input**. Answer in the panel; the answers go to the
  answer bank (untick the box to skip that) and JAP prepares the form again.

In Review mode (the default) the job then waits at **Ready for review**:
every answer is shown, with where it came from, and editable. Nothing is sent until you
press **Approve and submit**. JAP then opens the form fresh, fills it, presses Submit once,
and keeps a screenshot of the confirmation and a Playwright trace in `data/`. The job
becomes **Applied**, and its panel shows what was sent: the date, the resume version, every
answer, the screenshot and the frozen JD. One email, "Applied: Role at Company", goes to the
address on My profile through the configured sender (see [Sending email](#sending-email)),
with a link to the posting and to the job in JAP.

Submissions are at least 30 seconds apart and stop at the daily cap until midnight in
`JAP_TZ`. Submit is never pressed twice: if a retry finds it was pressed without a
confirmation, the job becomes **Failed** with "Submit outcome unknown" so you check by hand.
An Applied job has no Apply now, and a run never prepares it again, even when the employer
reposts it (a repost joins the same listing). **Skip** cancels a prepared or waiting job;
**Undo** brings it back. A **Failed** job shows the reason and screenshot, with **Retry**.

## Automatic mode

The **Review | Automatic** switch on the Runs card sets the Apply mode (A4). Turning it on
asks for its own agreement, because sending without showing you first is a different thing
to agree to ([What you agree to](#what-you-agree-to)). In Automatic mode
a job is submitted without asking you once its form is filled, unless something about it is
unclear (A3):

- a job whose posting doesn't say remote, hybrid or onsite (**Work mode unclear**) stops at
  Ready for review, with "Automatic mode left this one for you" and the reason;
- a question only you can answer, a required choice with no saved answer, a CAPTCHA or a
  failed cover letter still stops at **Needs input**;
- a job already waiting in Ready for review keeps waiting: the mode is fixed when a job is
  prepared, so switching takes effect from the next run.

A run in Automatic mode also picks only jobs at or above the **required-skills bar** (F6):
"Only jobs with at least N% of my required skills" on Settings, 60 by default. A job below
the bar, or with Skills not stated, is not prepared: it stays Not applied on Jobs for you to
apply to yourself, and its panel says so under Why it fits. With no required skills in Job
preferences, or the bar set to 0, there is nothing to measure and the bar is off. Review
mode has no bar, because you read every application before it goes.

Submissions are at most the **Daily cap** a day (next to the switch, 20 by default; set it to 2
to try Automatic mode) and at least 30 seconds apart. A run at the cap prepares nothing and
says "daily cap reached". The Daily run finds jobs only in Review mode, and finds and applies
in Automatic mode. Switching back to Review makes the next run prepare without submitting.

## When something fails

- **A job fails** (the form page is gone, the form changed, Submit gave no confirmation): after
  Inngest's retries it shows **Failed** with the reason, a screenshot of the page as JAP saw it
  (also when the form couldn't even be read), and **Retry**.
- **Alert emails** go to the address on My profile, once each: when a run fails, when a board
  in a run can't be fetched, when an application ends Failed (with the screenshot attached),
  and when the weekly form test can't read a form. They never carry answers or letters.
- **Spend cap**: My profile has an **AI spend** card with this month's spend and the monthly cap
  ($2, or `JAP_MONTHLY_COST_CAP_USD`). Once it is reached, model steps stop: job descriptions
  aren't read, answers aren't drafted, and no cover letter is written, so a job that needs
  one stops at Needs input ("Spend cap reached ...") and nothing is sent without it. The Runs
  card and the last run line say so. Lower the cap to $0.05 to try it, then set it back.
- **Daily backup**: an hourly Inngest cron (`daily-backup`) writes
  `data/backups/jap-YYYYmmdd-HHMMSS.sql.gz` with `pg_dump` the first time each day the Mac is
  awake, and keeps 14 days. **Back up now** on the Status card makes one at once;
  `make restore FILE=...` brings one back.
- **Weekly form test**: every Monday at 07:00 (`form-check`), JAP opens the newest open job's
  form on each ATS you watch and reads it, submitting nothing. The Status card shows the
  result per ATS; a failure sends an alert. **Test forms now** or `make form-check` runs it
  at once.
- **Dedup QA set**: `backend/tests/qa/dedup_pairs.yaml` holds 106 labelled job pairs (52 the
  same job, 54 different). `make qa` places each pair with the real dedup and prints the
  accuracy; CI fails below 95% or on any wrong merge. It found one: a bracket such as
  "(Python)" vs "(Java)" was dropped from the role, so the two jobs merged. Brackets now drop
  only places, work modes, requisition numbers and words like "full-time".

## Logs, Health and Insights

PRD 1.19 (O1 to O15), Part A. Everything runs on the Mac with the rest of JAP and costs
nothing; the only data that leaves is redacted errors sent to Sentry, and only when
`SENTRY_DSN` is set.

- **Logs**: the app and every Inngest function write one JSON line per event to stdout, with
  the ids it belongs to: request, run, application, posting, listing, Inngest run and
  function (`backend/observability/`). Every line is scrubbed first: email addresses, phone
  numbers, keys and tokens, `password=...` and the like, and emailed security codes are
  replaced, and fields such as answers, resume text and letters are never written.
  `LOG_JSON=false` prints plain lines instead. Docker keeps each container's lines capped at
  5 files of 50 MB (`make logs`). Every API response has an `X-Request-ID` header, so a
  problem can be found by its request.
- **Kept in Postgres**: warnings, errors and each apply step go to `log_event` for 30 days, and
  each API route's count, errors and time taken go to `route_stat` per hour. `make errors`
  shows the last 20 warnings and errors.
- **Settings > Health**: whether today's Daily search ran, searches stuck for over 30
  minutes, errors in the last 24 hours grouped by what failed, the slowest pages, disk use,
  and the alerts emailed in the last 7 days.
- **Health alerts**: `health-watch` runs every hour (at :40). It deletes log rows past 30 days
  and emails the address on My profile when the Daily search didn't start, a search is stuck,
  more than `JAP_ALERT_ERRORS_PER_HOUR` (10) errors happened in an hour, AI spend reached 80% or
  100% of the limit, or the disk is over 85% full. Each problem is emailed at most once a day
  (spend once a month). `JAP_ALERTS_ENABLED=false` turns them off; outside prod the subject
  starts with the environment, for example `[DEV]`. **Check now** or `make health-check` runs
  it at once. Failed searches, applications and form tests keep their own alerts.
- **What JAP did**: a failed or sent job's panel has a timeline of its application: the form
  read, fields mapped, each AI use with its time and cost, submit, and the confirmation email.
- **Insights** (new page): jobs found, matching, started, sent and failed per week; results by
  job site with the commonest failure reasons; AI spend this month by use and model, and the
  last six months with the cost per sent application.
- **Sentry** (optional, free Developer plan): create a project at sentry.io, put its DSN in
  `SENTRY_DSN` and restart. Only errors are sent, with no request bodies, cookies, headers or
  local variables, and every message scrubbed.
- **Uptime from outside** (O7): once prod is public, add a free uptime check (a Cloudflare
  health check, or a free monitor such as UptimeRobot) on `https://jap.tutorial.monster/api/health`
  every 5 minutes, emailing you when it fails. It works even when the Mac is asleep.

Part B comes after sign-in step 2: the user on every log line, Health for the admin only,
first-party usage counts (`usage_event`), each user's own Insights, and per-user counts and
spend for the admin.

## Cover letters

JAP writes a cover letter only when the application form makes its cover letter field
mandatory. An optional cover letter field stays empty, and a JD that asks for a letter on a
form with no letter field gets none. The letter is written right before the job reaches
Ready for review, as its own step (`cover-letter` in apply-listing):

- It is written from the frozen JD, your active resume and the **Cover letter tone** note on
  My profile, names the company and role, links two or three of your real skills or projects
  to what the JD asks for, and stays under 250 words.
- A second call lists every claim the letter makes about you and quotes your resume for each.
  Code keeps a claim only if the quote really is in the resume, and also checks the length,
  the company and role, and that every number in the letter is in the resume. Any problem
  rewrites the letter once with the problems listed; if the second letter fails too, the job
  stops at **Needs input**, where you can paste your own letter or Skip. Nothing is sent
  without a required letter.
- Like every model call, the prompt is redacted: the model sees `[NAME]`, `[EMAIL]` and
  `[PHONE]`, signs off as `[NAME]`, and your name is filled in locally. The Inngest UI shows
  the start of the redacted prompt in the `cover-letter` step's output, never the letter.
- The letter goes in as the field takes it: pasted as text, or uploaded as a PDF built from a
  plain template (your name and contact line, the date, role and company, the letter) and
  named like `Your-Name-Cover-Letter-Company.pdf`. The PDF is kept in `data/cover_letters/`.

In Ready for review the letter is editable, with a word count, the claim check and the PDF.
Your edited version is what is sent and what the confirmation email attaches; the Applied
panel shows the letter and the PDF as sent.

## Sending email

Every JAP email (Applied, alerts, the form test) goes to the address on My profile, sent over
SMTP from the mailbox set in `.env`. `make email-test TO=you@example.com` sends one test email.

**GoDaddy Professional Email** (the default): `SMTP_HOST=smtpout.secureserver.net`,
`SMTP_PORT=465`, `SMTP_SSL=true`, `SMTP_USERNAME` = the full address (you@yourdomain.com) and
`SMTP_PASSWORD` = that mailbox's password. These are GoDaddy's
[IMAP/SMTP settings](https://www.godaddy.com/help/use-imap-settings-to-add-my-professional-email-to-a-client-32204).

**Gmail**: `SMTP_HOST=smtp.gmail.com`, port 465, your Gmail address, and an app password from
https://myaccount.google.com/apppasswords (needs 2-Step Verification). An older `.env` with
`GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` still works when `SMTP_USERNAME` is empty.

After changing `.env`, run `make restart`. A send that fails is retried, then logged; the job
stays Applied and its panel says the confirmation email has not gone out.

## Greenhouse security codes

Greenhouse sometimes asks for an 8-character code it emails before it takes an application
(PRD A5). JAP reads that code from its own mailbox (the one in `.env`) over IMAP, types it in
and presses Submit once more. Set it up once:

1. In Gmail, Settings, Forwarding and POP/IMAP, add your JAP mailbox as a forwarding address.
   Gmail emails it a confirmation link: open that email in the JAP mailbox (GoDaddy webmail)
   and click it.
2. In Gmail, create a filter: From `greenhouse`, Has the words `security code`, then Forward it
   to the JAP mailbox.
3. `make code-mail-check` logs in over IMAP and counts the Greenhouse emails it can see.

GoDaddy's IMAP server is the default (`IMAP_HOST=imap.secureserver.net`, 993, SSL); for Gmail
use `IMAP_HOST=imap.gmail.com`. JAP uses only Greenhouse emails that arrive after its Submit,
waits up to 3 minutes, and never stores the code or sends it to a model. No code in time: nothing
was sent and the job becomes Apply yourself.

## Hosting: local and live

There are two environments. **local** is this working copy on your Mac. **live** is the
server: one Linux machine that runs the whole stack, is up whether or not your Mac is, and
is the only place that submits applications, sends email or holds anybody else's data
([PRD](https://claude.ai/artifact/WL9RYjsPDCTmknMLRqzKRj) H1 to H8).

| | local | live |
| --- | --- | --- |
| Where | your Mac | the server |
| Address | http://localhost:5173 | https://jap.tutorial.monster |
| Env file | `.env` | `.env.live`, on the server and nowhere else |
| Code | this working copy, live reload | the images GitHub Actions built for a version tag |
| UI | Vite dev server | `npm run build`, served by Caddy, which passes `/api` to the app |
| Ollama | native on the Mac, for the GPU | a container in the stack, on the CPU |
| Entry / API / Inngest / DB ports | 5173 / 8001 / 8290 / 5432 | 5373 / 8201 / 8490 / 5434, on 127.0.0.1 and reachable only over SSH |
| Data folder | `data/` | `data-live/` |
| Submits applications | **never** (`JAP_SUBMIT_ENABLED=false`) | **yes, for real** |
| Sends email | no | yes, the real mailbox |
| Daily search | leave off | on |
| Adminer | yes, `:8080` | not in the stack at all |
| Starts itself after a reboot | no | yes, every service |
| UI badge | **DEV** | none |

Every target takes `ENV=local|live`; without it you get local. On the Mac, `ENV=live` is for
reading and not for running: `make start ENV=live` refuses, because the images are
`linux/amd64` and because live is other people's data. live's targets run on the server.

### The server

One machine, and it will be enough for a long time: 4 vCPU, 8 GB, 80 GB of NVMe — a Hetzner
CX33 in Falkenstein is about €11 a month with its backups. Singapore is nearer to India but
roughly twice that, and visitors reach a Cloudflare edge near themselves either way, so only
the admin feels the difference.

The 8 GB is not for throughput. Postgres, Inngest, Caddy, the API, the worker and Ollama come
to roughly 1.9 GB together, and a Chromium filling in a form adds about 1 GB on top of that.
An out-of-memory kill *between pressing Submit and reading the answer* is the one failure JAP
cannot sort out by itself — the application becomes Failed with "Submit outcome unknown" and
somebody has to go and look at the employer's site — so the machine has room to spare, and
2 GB of swap underneath it.

Nothing listens on the internet. The site is served through JAP's own Cloudflare Tunnel, a
container in the stack making one outbound connection, and the firewall allows SSH and
nothing else. The tunnel reaches Caddy at `ui:8080` over the stack's own Docker network, so
not even the UI port needs publishing (H2).

**First time**

1. Make the machine (Debian or Ubuntu, your SSH key on it). Then, from this checkout:

   ```bash
   ssh root@<server> 'bash -s' < infra/scripts/server-setup.sh
   ```

   It installs Docker, makes a `jap` user, closes the firewall to everything but SSH, turns
   on unattended security updates, adds swap, and clones this repository to
   `/srv/public-jap`. Running it again changes nothing it has already done.

2. Cloudflare, Zero Trust, Networks, Tunnels: create a tunnel **for the server**, its own and
   not the Mac's. Two connectors on one tunnel makes Cloudflare share traffic between them,
   which during a move is the last thing you want. Copy its token.

3. On the server, as `jap`: `cd /srv/public-jap && make env ENV=live` writes `.env.live` with
   a fresh database password and Inngest keys. Put the four secrets into it by hand, once:
   `TUNNEL_TOKEN`, `OPENROUTER_API_KEY`, `SMTP_PASSWORD`, and `SENTRY_DSN` if you want errors
   reported.

4. `make deploy ENV=live TAG=vX.Y.Z` (below), and wait for `/api/health` to come back green
   on all four checks.

5. Only then add the public hostname in the Cloudflare dashboard: `jap.tutorial.monster` to
   `http://ui:8080`. That is the step that actually puts JAP on the internet, so it goes
   last. Add a Cloudflare rate-limiting rule on `/api/auth/*` at the same time.

6. Add an uptime check from outside — Better Stack and UptimeRobot both do it free — on
   `https://jap.tutorial.monster/api/health` every five minutes, matching the text
   `"ok":true`. This matters more than it looks: every alert JAP sends comes from
   `health-watch` running *inside* the stack, so the one thing it can never tell you about is
   the stack being down. Match on the text rather than the status code, because `/api/health`
   deliberately answers 200 with `"ok":false` when a dependency is unwell.

### Releasing

A version tag is the release. Pushing it builds the two images and pushes them to GHCR
(`.github/workflows/release.yml`), and the server checks out the same tag and pulls them, so
the compose files, the migrations and the running image are always one version.

1. Make the change and check it on local (`make start`, `make test`).
2. `git tag v0.21.0 && git push origin v0.21.0`, and wait for the Release workflow.
3. Back the database up first if the change has a migration (`make backup ENV=live`, on the server).
4. `ssh jap@<server> 'cd /srv/public-jap && make deploy ENV=live TAG=v0.21.0'`.

`make deploy` fetches tags, refuses a checkout with local changes, checks the tag out, pulls,
starts, waits for `/api/health` and migrates. **Rolling back is the same command with the tag
before it**, and takes about a minute: the old image is still in the registry and usually
still on the disk. If a migration changed data, restore as well
(`make restore ENV=live FILE=data-live/backups/<file>.sql.gz`).

### Backups

| What | Covers | Where it lives |
| --- | --- | --- |
| Hetzner automatic backups | the whole disk, 7 rolling days | the hosting account |
| `daily-backup` | `pg_dump` into `data-live/backups`, 14 days | the server's disk, and so inside the snapshot |
| `make backup-pull SERVER=<ssh host>` | the newest dump | **this Mac**, in `data/backups-live/` |

A disk snapshot of a running Postgres is crash-consistent: it replays its log on start, the
same as after a power cut. The `pg_dump` is what lets you put one table or one account back,
which a disk image cannot.

The third row is the one that is not really optional. The first two both live with the
server: lose the hosting account — closed, compromised, or a card that expired — and the
machine and every backup of it go at the same moment, and the database holds other people's
resumes and answers now. `make backup-pull` costs nothing, runs from the Mac, and leaves a
copy on hardware you own. Run it after anything important, and restore one occasionally: a
backup nobody has ever restored is a hope, not a backup.

### What is on the server and what is not

Adminer is not in the live stack, so there is no database browser on the public machine. The
admin screens — Accounts, Status, Health, Insights, duplicates, form tests — show counts,
dates, addresses and health, and never a user's resume, answers or jobs. Looking at the
database itself means an SSH tunnel to the server's `127.0.0.1` Postgres port, which means
being on the machine already.

Caddy sends a content security policy with every page, which allows scripts from JAP itself
and Cloudflare Turnstile and nothing else, so adding anything to the UI that loads from a new
host means adding it to `infra/caddy/Caddyfile` too. The sign-up page is the one to check
afterwards, because Turnstile is the only third-party script JAP loads.

## Make targets

| Target | Does |
| --- | --- |
| `make start` | Build and start the stack, wait for `/api/health`, apply migrations, check Ollama |
| `make stop` | Stop the stack (data kept) |
| `make reset` | Asks first, then wipes the database: down -v, up, migrate, seed |
| `make clean` | Asks first, then removes containers, volumes, JAP images and build caches; `data/` is kept |
| `make logs` / `make ps` | Follow logs / list services |
| `make migrate` | `alembic upgrade head` in the app container |
| `make seed` | Load `seed/companies.yaml` and `seed/skills.yaml` |
| `make test` | ruff, mypy, pytest for app; lint and build for ui; the coding-agent guards (`make test-agents`) |
| `make setup-host` / `make test-host` | The same checks without the Docker stack, for cloud sandboxes: set up `.venv`, Chromium, `node_modules` and a Postgres once, then run them |
| `make worktree NAME=...` | A sibling worktree on its own branch with its own stack, ports and `data/`, for an agent working alongside you; `worktree-list` and `worktree-remove NAME=...` go with it |
| `make hooks` | Turn on the pre-commit check for this clone: refuses secrets files, `docs/`, `data/` and anything that looks like a key |
| `make backup` / `make restore FILE=...` | Dump to / restore from `data/backups/` (the app also makes one every day) |
| `make backup-pull SERVER=...` | Copy the server's newest dump to this Mac: the one backup that does not live with the server |
| `make close-unfollowed` | List open postings of companies you no longer follow; `APPLY=1` closes them (back up first) |
| `make code-mail-check` | Log in to the mailbox over IMAP and count the Greenhouse emails it holds (security codes, PRD A5) |
| `make qa` | Dedup QA set: place the 106 labelled job pairs and print the accuracy |
| `make form-check` | Read one live form per ATS now (the weekly form test); nothing is submitted |
| `make set-password EMAIL=...` | Set a new password for an account from the machine (asks twice) |
| `make two-step-off EMAIL=...` | Turn two-step sign-in off for an account (phone and recovery codes lost) |
| `make sign-out-all EMAIL=...` | End every session of an account |
| `make health-check` | Run the hourly health watch now: old log rows deleted, alerts that are due emailed |
| `make errors` | The last 20 warnings and errors from `log_event` |
| `make email-test TO=...` | Send one test email from the mailbox in `.env` |
| `make llm-check` | One tiny test call to each OpenRouter model (no personal data) |
| `make ollama-check` | Is Ollama answering with `nomic-embed-text` pulled, at the address the app really uses |
| `make demo` / `make demo-clear` | Load or remove the demo postings used to check dedup and the filters |
| `make env ENV=live` | Write `.env.live` with a fresh database password and Inngest keys (on the server) |
| `make deploy ENV=live TAG=...` | On the server: check out a release tag, pull its images, start, migrate. Rollback is the same with the tag before |
| `make tunnel-status` | Is the Cloudflare Tunnel connected? (it is a service of the live stack, so `make start`/`stop ENV=live` is what turns it on and off) |

## Layout

```
Makefile            .env.example
infra/              compose.yaml, compose.release.yaml (live), docker/ (Dockerfiles),
                    caddy/, env/live.env, postgres/init.sql, scripts/
seed/               companies.yaml, skills.yaml
backend/            main.py, config.py, schemas/, database/ (+ migrations/), api/,
                    sources/, pipeline/, llm/, functions/ (Inngest), tests/
frontend/           src/pages, src/components, src/api
data/               gitignored: resumes, screenshots, cover letters, backups
.github/workflows/  CI: ruff, mypy, pytest; ui lint and build; compose config check; agent guards
AGENTS.md           the rules for coding agents; CLAUDE.md, .devin/, .claude/, .gemini/ and
                    .cursorignore only point at it and enforce it (AGENTS.md, Harnesses)
```

## Version history

| Version | Date | Changes |
| --- | --- | --- |
| 0.21.0 | 20 Nov 2026 | live runs on a server instead of the Mac (PRD H1 to H8; IDEA-10 to IDEA-14). One Linux machine is up whether or not the Mac is, and nothing about local changes. **Two halves** (H2): the app image runs twice, `JAP_ROLE` apart — `app` answers `/api` and does not mount `/api/inngest` at all, `worker` runs the Inngest functions and the Playwright browser and is published nowhere, so the endpoint that runs JAP's functions is absent from the process a browser can reach rather than merely guarded. Both exist on local too, so dev reproduces live, and Phase 4 adds workers by raising a replica count. **Releases**: live builds nothing. A `v*` tag makes GitHub Actions push `public-jap-app` and `-ui` to GHCR, and `make deploy TAG=vX.Y.Z` on the server checks that tag out and pulls them; the image tag comes from the checked-out tag, so the compose files and what runs cannot drift apart, and rolling back is the same command with the tag before. The images are `linux/amd64` and starting the release stack refuses on a Mac. **The machine**: `infra/scripts/server-setup.sh` prepares a fresh Debian — Docker, a `jap` user, a firewall open only to SSH, unattended security updates, and swap, because an out-of-memory kill between pressing Submit and reading the answer is the one failure JAP cannot resolve by itself. Ollama becomes a container (the server has no GPU to use) and cloudflared joins the stack, reaching Caddy over the Docker network, so the server publishes no inbound port at all and `infra/tunnel/` is gone. **Keeping less** (IDEA-14): traces 30 days and screenshots 180, swept hourly by health-watch — files only, because an Applied application is frozen (A2) and both screens already ask the filesystem. **Hardening** (IDEA-13): a write is refused when `Sec-Fetch-Site` says it began on another site, not only when `Origin` does; Caddy sends a content security policy, HSTS and the rest; and the board reader refuses any address that is not an ATS on every hop, so a redirect cannot aim JAP at the server's metadata service. **Backups**: `make backup-pull` copies the server's newest dump to the Mac, the only copy that survives losing the hosting account |
| 0.20.0 | 20 Nov 2026 | Consent, your data back or gone, searches side by side and Google sign-in (PRD S14, S15, S16, T6, U22). **Consent** (`0015_consent`): the terms and privacy notice are ticked at sign-up and the version ticked is kept, so raising `TERMS_VERSION` asks everyone again; accounts from before are asked once on their next visit, because a consent written by a migration is one nobody gave. Every `/api` route but health and `/api/auth` now carries that tick, not only the screen. An opt-in before JAP applies in your name and a second before it does so without asking; taking the first back takes the second with it and returns the account to Review mode, and Submit reads it again at the last moment, so an attempt approved before it was withdrawn fails instead of being sent. **Your data** (`0016_erasure`): Download my data gives a zip of every private table plus the files, and Delete my account removes all of it at once, with no undo; both work even for an account that will not agree to the notice, or the gate would be a trap. `llm_call` and `log_event` outlive it with the user nulled, holding no personal content. Deleting an Applied application is still refused everywhere except inside the erasure transaction. **Searches side by side** (T6): `run-pipeline` takes a concurrency keyed on `user_id` under a `JAP_MAX_PARALLEL_RUNS` ceiling, so two people search at once and only your own search holds yours up; a board fetched within `JAP_BOARD_FRESH_MINUTES` is reused rather than fetched again, while still reading descriptions nobody had asked for yet. **Time zone** (U22): the daily search runs in each account's own zone, set beside the time in Settings. **Google sign-in** (`0017_user_identity`): OpenID Connect with PKCE beside the password, linking to an account that already exists only when Google says the address is verified, Connect and Disconnect in Settings, and the button hidden unless the `GOOGLE_` settings are filled in |
| 0.19.0 | 17 Nov 2026 | Each user's own data and open sign-up (PRD 1.18 step 2: S7, S10, S12, S13). Facts about jobs stay shared and everything about a person is private: every private table carries a `user_id`, and `board_follow` and `user_listing` hold the personal half of what sat on shared rows (migration `0011_tenancy`; existing data goes to the admin). Every route and background job works for one user and another user's id answers Not Found; Postgres repeats the check with row-level security (`0013_rls`). A company two people follow is fetched once and each description is read by the AI once; a role-scoped search stores the whole board, reading a posting uses no profile details, and Merge and Not the same job are the admin's. Anyone can create an account and confirms the address by an emailed link (`0012_email_verify`), with limits per address and per email and optional Cloudflare Turnstile. The free plan is 1 company, 1 application a day and $1 of AI a month, shown in Settings as Your plan; the admin can set any one account's own limits (`0014_account_limits`), turn an account off and pause sign-up from Settings > Accounts. Reading shared descriptions has the service's own budget and a total limit spans everything. System status, Health, backups, form tests and duplicates are the admin's. Files are written under `data/users/<user id>/` |
| 0.18.1 | 13 Nov 2026 | Fix the four settings you type rather than tick saving only when the field lost focus (JAP-6). Settings says changes save straight away, and every radio and switch does, but "Send at most N applications a day", "Only jobs with at least N% of my required skills", the daily search time and the monthly AI limit waited for a click somewhere else: Enter did nothing at all and there was no button, so a typed number looked ignored. Enter saves now, so does a Save button that appears beside the field as soon as the value differs from the saved one, Escape forgets the edit, and leaving the field still saves so nothing typed is lost. What was typed stays on screen until the save comes back, instead of showing the old value again in between |
| 0.18.0 | 13 Nov 2026 | Skills match instead of a skills filter (PRD 1.21, F5, F6, U15, U17, U18). Skills never hide a job: the Skills condition and the third Matching choice are gone, so matching is job title and location only, and the default rule is job title and location everywhere (the code fallback and the API default used to be any one of title, skills or location). Every job gets a skills match in the same SQL pass as the filters (migration `0010_skills_match`: `listing.required_hit`, `nice_hit`, `required_pct`): Required x of y with the percentage and the missing skills, Nice to have x of y, and Skills not stated when the description gave none, which is unknown and never 0. Jobs has a Skills match column that sorts, and a minimum filter (`sort=skills`, `min_skills`); the job panel lists the skills behind it. Automatic mode picks only jobs at or above a required-skills bar, 60% by default, set on Settings (`profile.auto_apply_min_skills`); the rest stay listed. A rule saved earlier with skills keeps its other conditions. |
| 0.17.1 | 13 Nov 2026 | Fix a job whose title is wider than the Job title column showing as nothing but `...` (JAP-5). The title is a button so it can be opened from the keyboard, and a button is `display: inline-flex`, which makes it an atomic inline box: `text-overflow` can shorten text but cannot cut into one. Chrome therefore dropped the whole button and drew the ellipsis in its place, so the row lost its title entirely while the side panel still showed it. The row is a flex line now and the button shortens its own text, so a long title reads "Applied AI Architect, Democrati…" |
| 0.17.0 | 13 Nov 2026 | Unfollow and re-follow (PRD 1.20, I10 to I18). Stopping following a company used to only switch its board off: its postings stayed open for ever, were never read, and kept "Reading N job descriptions" and "Searching…" on screen. Now it closes the board's open postings with the reason `unfollowed` (migration `0009_close_reason`; the other reason is `gone_from_board`), closes the jobs left with no open posting on a followed board, and nothing is deleted. Jobs with an application stay in the list with a note. No AI reading is done for an unfollowed company, queued reads included, and a search that was running cannot reopen its jobs. Following again switches the same board back on and the next search reopens the same rows, re-reading only descriptions that changed. A confirmation says how many jobs will be hidden; the search line says how many were reopened. `make close-unfollowed` closes what earlier unfollows left open |
| 0.16.2 | 13 Nov 2026 | Fix Profile claiming there is no resume for about a second on every visit. Switching tabs unmounts the page, so its resume list started empty again each time and the section drew "No resume yet. JAP needs one before it can apply.", without the skills found in the resume, until `/api/resumes` answered. On live that is roughly a second, because every call crosses the Cloudflare Tunnel, while locally it is 5ms, which is why it never showed in dev. Profile now paints the active version the store already caches and only says there is no resume once the list has really arrived; Saved answers waits the same way instead of flashing "No saved answers yet". An upload writes the new version back to the store, so the next visit paints that one and not the one before it |
| 0.16.1 | 12 Nov 2026 | Fix the foot of the sidebar: a long email address stretched the single grid column past the panel, so the line above Sign out and the Sign out button itself were drawn over the work area. The column is `minmax(0, 1fr)`, so the address is cut with an ellipsis (the full one stays in the tooltip) and the row ends where the panel does. Dropdown arrows no longer crowd the field border: Chrome pins the native arrow 4 or 5px from the edge whatever the padding is, so every select draws its own chevron at the same 10px inset as its label, which also makes selects exactly as tall as the search box beside them. The version under the logo said 0.15.0 |
| 0.16.0 | 4 Nov 2026 | Two environments instead of three (PRD H1 to H8 revised): `ENV=local\|live`, both from this one checkout, with their own env file, Docker project, database volume, data folder (`data/`, `data-live/`) and ports. `live` is the release stack behind the Cloudflare Tunnel at jap.tutorial.monster and the only one that submits or sends email; `local` never does either. Removed the test environment and the git worktrees in `~/jap-envs`, with `make env-create`, `promote`, `mark-passed` and `rollback`; a release is now `make start ENV=live`. `JAP_DATA_HOST_DIR` keeps the two data folders apart. README says plainly that JAP is a personal, single-user tool |
| 0.15.0 | 2 Nov 2026 | Logs, Health and Insights (PRD 1.19, Part A: O1 to O12, O15): JSON log lines with request, run, application and Inngest ids, scrubbed of personal data and secrets; warnings, errors and apply steps kept 30 days in `log_event`, API timings per route in `route_stat`; Docker log cap; `X-Request-ID`; Settings > Health; hourly `health-watch` with alert emails at most once a day; a timeline on each application; the Insights page; optional Sentry (`SENTRY_DSN`); `make health-check` and `make errors`; migration `0008_observability` |
| 0.14.0 | 2 Nov 2026 | Hosting with dev, test and prod (PRD 1.17, H1 to H8): every make target takes `ENV=dev\|test\|prod`; test and prod run from git worktrees in `~/jap-envs` with `infra/compose.release.yaml` (built UI behind Caddy, self-hosted Inngest, no Adminer, restart on Docker start); `make env-create`, `promote`, `mark-passed`, `rollback`, `tunnel-start/stop/status`, `mac-wake`; the UI calls `/api` on its own address; `JAP_ENV` badge; `JAP_SUBMIT_ENABLED=false` in test never submits |
| 0.13.0 | 2 Nov 2026 | Sign-in and accounts, step 1 (PRD 1.18, S1 to S9, U21). Sign-in screen before anything loads: create the first account (the admin; sign-up then closes), sign in, Forgot password with an emailed single-use 30-minute link, and the two-step code. Settings > Account: Change password, Two-step sign-in with a QR code and 8 recovery codes, Sign out and Sign out everywhere; Sign out also at the foot of the sidebar. Backend: `app_user`, `auth_session` and `password_reset` (migration 0007), scrypt hashes, a 15-minute lock after 5 wrong tries, TOTP with replay protection, HttpOnly host-only session cookies (7 days idle, 30 days at most; `__Host-` and Secure with `JAP_COOKIE_SECURE`), every `/api` route but health and `/api/auth/*` behind a session, `/api/inngest` closed to proxied requests, writes from other origins refused. `make set-password`, `make two-step-off`, `make sign-out-all`; `make form-check` now runs inside the app container |
| 0.12.0 | 31 Oct 2026 | Greenhouse emailed security code (PRD 1.11, A5): when the form asks for the code it emailed, JAP reads it from its own mailbox over IMAP (`IMAP_HOST`, `IMAP_PORT`, `IMAP_SSL`, same login as SMTP) after a Gmail filter forwards it there, using only Greenhouse emails that arrive after Submit and waiting up to 3 minutes; types it into the boxes and presses Submit once more; no code, nowhere to type it or a code the site refuses means nothing was sent and the job becomes Apply yourself; `make code-mail-check`; Greenhouse security codes section |
| 0.11.2 | 31 Oct 2026 | Apply fixes from the first real Automatic runs. Greenhouse: a combobox counts as filled when it changed from the fresh page (the phone Country picker shows only "+91"), values are read back after a short settle, a phone the page reformats still matches, and a field that won't keep its value is named with what the page shows. Ashby: Yes/No questions made of buttons are read as a choice (answer bank or Needs input) and filled by clicking the button; the page's own error text after Submit is in the error, and a "required" or spam-check error counts as not sent. "Successfully submitted" wording is taken as a confirmation. Greenhouse's emailed security code step is recognised and reported as not sent, to apply yourself |
| 0.11.1 | 31 Oct 2026 | Fix "OpenRouter 402" on drafted answers: every model call now sets `max_tokens` (about 3 characters a token plus room for the JSON for an answer, a fixed cap per purpose otherwise) instead of reserving the model's whole 64,000-token output against the credits; a 402 shows as "OpenRouter is out of credits" without the key link, stops further drafts on the same form, fails the cover letter without a retry and does not retry posting extraction or resume parsing; a reply cut off at the cap is reported as such |
| 0.11.0 | 31 Oct 2026 | Email from any SMTP mailbox (PRD 1.10, E4): `SMTP_USERNAME` and `SMTP_PASSWORD` with GoDaddy Professional Email as the default server (`smtpout.secureserver.net`, 465, SSL), Gmail with an app password as the alternative, old `GMAIL_*` settings still read; `make email-test`; Sending email section |
| 0.10.0 | 30 Oct 2026 | New design (PRD 1.5, mockup v0.5). Sidebar with Jobs, Profile, Job preferences and Settings in place of the Dashboard and My profile tabs; Jobs with a status strip, search first, Job title, Location, Work mode, Posted and Fits on filters, sortable columns, rows per page and page numbers, tick-to-Apply and Hide with Undo, a one-line duplicates bar and an AI limit banner; one job panel whose actions follow the status (Apply, Save and continue, Send application, Try again, Apply yourself, Undo hide) with Why it fits and Not the same job; Find new jobs with an arrow for some job titles and Also apply; Job preferences with office cities and On-site/Hybrid ticks, remote jobs as none, your countries or all (`profile.remote_any`, migration 0006), three Matching choices (a new profile starts with job title and location), and a save bar that counts the jobs that will fit; Settings with Applying, Daily search, Search history, AI usage and System status; contact-only saves start no search; `GET /api/listings` takes sort, dir, work_mode, posted_days and status=active, and search covers locations; while testing: a / tooltip on the search box, the last search line coloured by state, and borderline duplicates settled by the AI instead of asking (no Merge list; Not the same job stays) |
| 0.9.2 | 30 Oct 2026 | Fix: Approve on Greenhouse job-boards pages left every field empty. Submit waits for the page to settle, reads every value back and fills again what the page cleared, and stops before Submit (nothing sent) when a field still won't keep its value; dropdowns built from a text input (comboboxes) are read as selects with their options and filled by picking the option; a form that refuses the submission is reported as not sent; Retry after a Submit whose outcome is unknown asks you to check the employer's site first |
| 0.9.1 | 30 Oct 2026 | Fix: Apply now didn't draft free-text answers. The answer prompt (now `answer.v2`) asks for JSON like every other prompt, `llm.call()` accepts JSON in a code fence or a sentence and logs a reply that isn't JSON as an error, a draft the writer model can't make shows as "JAP couldn't draft an answer" with the reason on Needs input (with a text box to write your own) and as "answers not drafted" on the last run line |
| 0.9.0 | 29 Oct 2026 | Phase 8: Automatic mode and hardening. Review/Automatic switch and Daily cap on the Runs card, jobs with an unclear work mode held at Ready for review with the reason (A3), Daily run finds and applies in Automatic mode, "daily cap reached" and "spend cap reached" on the last run line, spend cap set on My profile (AI spend card), screenshot kept when a form can't be read, alert emails for failed runs, boards, applications and form tests (notify-failure), daily pg_dump backup (daily-backup, 14 days kept), weekly live form test per ATS (form-check), Backups and form tests on the Status card, dedup QA set of 106 labelled pairs (`make qa`) and the bracket fix it led to, `make form-check`, `make help` fixed when `.env` exists |
| 0.8.0 | 29 Oct 2026 | Phase 7: cover letters. Written only for a mandatory cover letter field (`cover_letter` prompt, as its own `cover-letter` step), claim check against the resume (`claim_check` prompt plus code checks for quotes, length, company and role, numbers), one rewrite, Needs input after two failures with your own letter accepted, text or PDF delivery, editable letter in Ready for review, letter and PDF in What was sent and attached to the email |
| 0.7.1 | 29 Oct 2026 | Match rule fix: the rule in words, a warning for rows that can't narrow the list, one-click rules (Any one, Role and Location, Role and Skills, All three), per-condition counts in the preview, and locations typed with their region ("Bangalore, India") match by city |
| 0.7.0 | 29 Oct 2026 | Phase 6: Apply in Review mode. Apply now and Skip in the job panel, apply-listing (JD snapshot, Playwright form reader, contact fields from My profile by code, resume upload, answer bank, drafted answers for required free text), Needs input with answers saved to the answer bank, Ready for review with every answer editable, Approve and submit (daily cap, 30 s gap, Submit pressed at most once), confirmation screenshot and trace, What was sent with the frozen JD, "Applied: Role at Company" email, Find and apply in the Run dialog, apply counts on the last run line, Retry for Failed; README history row for 0.6.0 moved here from the services table |
| 0.6.0 | 29 Oct 2026 | Phase 5: Run dialog with role scope, Daily run switch and time (minute cron in `JAP_TZ`), background run after a profile save, run after accepting resume skills, one run at a time with repeats merged into the queued run, Runs card with last run line, run in progress and queued run |
| 0.5.0 | 29 Oct 2026 | Phase 4: match rule as one SQL query (Role, Skills, Location, Remote regions, work mode, exclusions), re-filter on profile save and after every run, match preview on My profile, Totals and Jobs by role, filter bar (role, location, status, matched condition), matched-condition tags, Globex Demo jobs in `make demo` |
| 0.4.0 | 29 Oct 2026 | Phase 3: canonical company and role, dedup into listings (requisition ID, exact and near match), Possible duplicates with Merge and Keep apart, Merge and Split in the job panel remembered across runs, merged count on the last run line, `make demo` |
| 0.3.0 | 29 Oct 2026 | Phase 2: Greenhouse, Lever and Ashby adapters, board detection from a URL or name, Find only runs (run-pipeline, fetch-board, enrich-posting), extraction cached by description hash, Closed postings, job list with the JD side panel |
| 0.2.2 | 29 Oct 2026 | My profile: "No unsaved changes" next to a disabled Save, an inline hint when no work mode is ticked, and work modes compared as a set |
| 0.2.1 | 29 Oct 2026 | Writer model is `anthropic/claude-sonnet-4.5` (normal calls); OpenRouter only takes `:batch` ids through its batch service |
| 0.2.0 | 29 Oct 2026 | Phase 1: full schema (0001_initial), My profile, resume upload and parsing, redact module, model spend log and $2 cap, Companies to watch, answer bank |
| 0.1.2 | 29 Oct 2026 | Inngest host port 8290, since another Inngest dev server also takes 8289 |
| 0.1.1 | 29 Oct 2026 | Host ports set in `.env`; API on 8001 and Inngest on 8289 by default |
| 0.1.0 | 29 Oct 2026 | Phase 0: repo layout, Docker Compose (ui, app, inngest, db, adminer), Makefile, FastAPI health and Inngest endpoint, status page, CI |
