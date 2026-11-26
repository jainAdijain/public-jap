# Jobs: fetching, matching, searching and applying

**Requirements:** JOBS-1 to JOBS-8, APPLY-1 to APPLY-8, TEN-7 ([requirements.md](../intent/requirements.md)). The tests that
prove each one are in [traceability.md](traceability.md); a rule here with no test is a gap
there.

The long form, screen by screen, is `README.md` (Fetching jobs to Greenhouse security codes).
This page is the behaviour in one place, with where it lives.

## 1. Following companies and fetching boards

- A user adds a company by pasting its Greenhouse, Lever or Ashby board URL or typing its name
  (`backend/sources/detect.py`). Following is a `board_follow` row; the board itself
  (`watched_board`) is shared. The plan caps how many a user follows.
- `fetch-board` reads a whole board, throttled to one request a second per ATS, refusing any
  address that is not an ATS on every redirect hop. A board fetched within
  `JAP_BOARD_FRESH_MINUTES` (10) for anyone is reused (`boards_fresh`).
- `enrich-posting` has the extract model read each **new or changed** description once
  (cached by description hash), billed to the service, redacted generically, and only when
  the title is a target role of someone who follows that company.
- **Unfollow** closes the board's open postings with reason `unfollowed` and drops its jobs
  from that user's list, except jobs with an application. Nothing is deleted; no AI reading
  is done for it after. **Following again** reopens the same rows on the next search;
  postings gone meanwhile close as `gone_from_board`. (`pipeline/unfollow.py`, PRD 1.20.)

## 2. One row per job (dedup)

Postings group into **listings**, one per company and canonical role
(`pipeline/canonical.py`, `pipeline/dedup.py`). Company names are cleaned and aliased; titles
reduced to a canonical role with seniority kept. Postings join on requisition id, same role,
or a near match (titles 90% alike, descriptions 92% by embedding). Pairs just below (75% /
85%) are settled by the extract model after each search's dedup. "Not the same job" splits a
wrong join and is remembered. `make qa` places 106 labelled pairs; CI fails under 95% or on any
wrong merge.

## 3. Which jobs a user sees (matching)

One SQL query over stored listings per user (`pipeline/matching.py`), writing `user_listing`.

- The rule is rows of Role and Location; Matching is "job title or location" or "job title and
  location" (default). Role: the cleaned title contains a target role. Location: a job
  location names one of the user's, or a remote job allows one of their regions, or it is
  Remote - Worldwide, or they chose All remote jobs. Then work mode must be ticked, and
  excluded companies and title words drop out.
- **Skills never hide a job** (PRD 1.21, F5). The same pass writes a skills match
  (`required_hit`, `nice_hit`, `required_pct`); "Skills not stated" is unknown, not 0.
- Saving Job preferences re-filters in well under a second without fetching.

## 4. Searches (runs)

A run is one account's: it fetches the boards that account follows and applies with its
settings. Started by **Find new jobs** (optionally narrowed to some titles and with "also
apply"), the **daily search** (per account, in its own time zone, cron every minute, missed
runs skipped not caught up), **saving Job preferences**, or **accepting resume skills**.

One search at a time per account, up to `JAP_MAX_PARALLEL_RUNS` accounts at once. A trigger
during your run queues one run behind it; more triggers join that one.

## 5. Applying

**Review mode** (default). Apply (or tick rows, or "also apply") asks once for consent to
apply in the user's name, then `apply-listing`:

1. freezes the job description, opens the public form in headless Chromium (Playwright) and
   lists every field;
2. fills **contact fields by code** from the profile (no model sees them), uploads the active
   resume, takes other answers from the answer bank (exact or 80% alike), drafts required
   free text with the writer model (redacted);
3. stops at **Needs input** for questions only the person can answer (notice, salary,
   authorisation...), a required choice with no saved answer, a missing file, or a CAPTCHA;
4. writes a cover letter **only when the form requires one** (claim-checked against the
   resume, one rewrite, then Needs input);
5. waits at **Ready for review** with every answer editable. **Send application** reopens the
   form fresh, fills it, presses **Submit once**, keeps a screenshot and trace, and marks the
   job **Applied** (frozen). One email goes to the user.

If a retry finds Submit was pressed without a confirmation, the job becomes **Failed** with
"Submit outcome unknown". `JAP_SUBMIT_ENABLED=false` (every local stack and worktree) never
submits.

**Automatic mode** (A3, A4) needs its own consent. A filled form is submitted without asking
unless the work mode is unclear (waits at Ready for review with the reason), something needs
the person (Needs input), or the job is below the **required-skills bar** (default 60%, not
prepared). Mode is fixed when a job is prepared. Automatic is also the admin's to allow,
account by account (launch decision D2): without that the account is held to Review mode and
a job is prepared in Review mode whatever the profile says (APPLY-8,
[plans-and-limits.md](plans-and-limits.md)).

**Limits** are in [plans-and-limits.md](plans-and-limits.md): daily cap, gaps per account and
per ATS, service-wide ceiling, browsers at once, Pause sending.

## 6. Greenhouse security codes (A5, IDEA-16)

Greenhouse may email an 8-character code before taking an application. Three routes, the form
takes whichever answers first:

- each account's own address `jap+<token>@<mailbox>`: the user forwards Greenhouse codes to it
  with one mail filter, and JAP reads the mailbox over IMAP, matching on the **recipient**;
- an inbound-email service posting to `/api/inbound/email` with `JAP_INBOUND_SECRET`;
- the user typing it into the job panel.

Only emails after Submit count, JAP waits up to 3 minutes, and the code is never logged or
sent to a model; it sits on the application row only while the form is open. No code: nothing
was sent, and the job becomes Apply yourself.

## 7. When something fails

Failed jobs show the reason, a screenshot and Try again. Alert emails go to the user once each
for a failed run, an unreadable board, a Failed application and a failed weekly form test.
`health-watch` alerts the admin about missed daily searches, stuck searches, error bursts, AI
spend at 80%/100%, and disk over 85%. Per-ATS outcomes (confirmed, refused, asked for a code)
are recorded so blocking shows up early (Settings, admin).
