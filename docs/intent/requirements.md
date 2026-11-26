# Requirements

Every requirement public-jap holds itself to, with an id named after its area. The PRD is the
source of truth but is private (`docs/intent/sources/`), so this page is its public summary;
where an id like `S14` or `A2` appears under Source, it is the PRD's own id. Where there is no PRD
id yet the source is the `IDEA-N`, a decision in [decisions.md](decisions.md) or the version that
built it.

Rules for this page:

- An id is never reused or renumbered. A requirement that is dropped stays, marked **Dropped**.
- A new requirement gets the next number in its area, a row in
  [traceability.md](../behaviour/traceability.md) in the same pull request, and a phase in
  [plan.md](plan.md).
- Phase 0 is single-user JAP before the fork; everything since is in [plan.md](plan.md).

Areas: `AUTH` accounts and sign-in, `TEN` one user's data kept from another's, `CONSENT`
agreements, `DATA` a person's data back or gone, `PRIV` models and logs, `JOBS` fetching, dedup,
matching and searches, `APPLY` applying, `BILL` limits, AI budgets and paying, `OPS` running it.

## Accounts and sign-in

| Id | Requirement | Source | Phase | Kind |
| --- | --- | --- | --- | --- |
| AUTH-1 | Anyone can sign up; every account after the first confirms its address by an emailed single-use link, and the answer is the same whether or not the address is taken | PRD 1.18 | 1 | feature |
| AUTH-2 | The first account is the admin; `JAP_ADMIN_EMAIL` restricts it to one address | v0.13.0 | 0 | security |
| AUTH-3 | Sign-up and sign-in tries are limited per visitor and per address, and five wrong passwords lock sign-in for 15 minutes | PRD 1.18 | 1 | security |
| AUTH-4 | Sessions are HttpOnly host-only cookies that end after 7 days idle or 30 days, and can be ended everywhere | v0.13.0 | 0 | security |
| AUTH-5 | Two-step sign-in with TOTP and single-use recovery codes | v0.13.0 | 0 | security |
| AUTH-6 | A forgotten password is reset by an emailed single-use link that expires and ends other sessions | v0.13.0 | 0 | feature |
| AUTH-7 | Sign in with Google is optional, and joins an existing account only when Google says the address is verified | S16, IDEA-1 | 2 | feature |
| AUTH-8 | Every `/api` route needs a session except a listed open set, and adding to that set is a decision | AGENTS.md | 0 | security |
| AUTH-9 | A write that began on another site is refused | IDEA-13 | 3 | security |
| AUTH-10 | The admin can pause sign-up and turn an account off; a turned-off account cannot sign in and its work stops | PRD 1.18 | 1 | feature |

## One user's data kept from another's

| Id | Requirement | Source | Phase | Kind |
| --- | --- | --- | --- | --- |
| TEN-1 | Every private table carries a user and a row-level security policy; with no user in scope a query returns nothing | decision 2 | 1 | security |
| TEN-2 | An id that belongs to another user answers 404 on every route, the same as one that does not exist | decision 3 | 1 | security |
| TEN-3 | Facts about jobs are shared and fetched once; each user's list is their own, and hiding, applying or unfollowing changes only theirs | decision 1 | 1 | feature |
| TEN-4 | Files are kept per user and served only through the row that owns them | PRD 1.18 | 1 | security |
| TEN-5 | Background work runs as the user whose event started it | PRD 1.18 | 1 | security |
| TEN-6 | Counts across users (pacing, the service-wide ceiling) are read as numbers, never as other people's rows | IDEA-17 | 4 | security |
| TEN-7 | A security code is matched to the account by the address it was sent to, never by "newest in the mailbox" | IDEA-16 | 4 | security |
| TEN-8 | The admin sees counts, dates, addresses and health, never anything a user wrote | IDEA-12 | 3 | privacy |

## Agreements

| Id | Requirement | Source | Phase | Kind |
| --- | --- | --- | --- | --- |
| CONSENT-1 | The terms and privacy notice are agreed at sign-up, the version agreed is stored, and a newer notice asks again before anything else | S14 | 2 | privacy |
| CONSENT-2 | Nothing is prepared or sent until the user agrees JAP may apply in their name; taking it back stops an attempt that is waiting | S14 | 2 | privacy |
| CONSENT-3 | Automatic mode needs its own agreement, and taking back the narrower one takes it too | S14 | 2 | privacy |

## A person's data back or gone

| Id | Requirement | Source | Phase | Kind |
| --- | --- | --- | --- | --- |
| DATA-1 | Download my data holds everything of mine and nothing of anyone else's, and works without agreeing to a new notice | S15 | 2 | privacy |
| DATA-2 | Delete my account takes everything of mine at once and nothing of anyone else's, needs the password and the address typed out, and stops any payment first | S15 | 2 | privacy |
| DATA-3 | Traces, screenshots and log rows are kept only for their retention period | IDEA-14 | 3 | privacy |
| DATA-4 | Every claim the privacy notice makes is a test | IDEA-23 | 6 | privacy |

## Models and logs

| Id | Requirement | Source | Phase | Kind |
| --- | --- | --- | --- | --- |
| PRIV-1 | No personal data reaches a model: it is redacted, and a guard refuses any request that still carries some; contact fields are filled by code | decision 10 | 0 | privacy |
| PRIV-2 | One file talks to OpenRouter, and every call is redacted, capped, logged with its cost and validated | decision 11 | 0 | security |
| PRIV-3 | Logs are scrubbed of emails, phones, keys, tokens and codes; answers, resumes and letters are never logged | AGENTS.md | 0 | privacy |

## Jobs: fetching, dedup, matching, searches

| Id | Requirement | Source | Phase | Kind |
| --- | --- | --- | --- | --- |
| JOBS-1 | A company is followed by its Greenhouse, Lever or Ashby board URL or name, and JAP fetches nothing but those job sites, on every redirect | v0.3.0, v0.21.0 | 0 | feature |
| JOBS-2 | Postings of the same job become one listing, at 95% or better on the labelled pairs and with no wrong merge | v0.4.0 | 0 | feature |
| JOBS-3 | A user sees jobs matching their role and location rules; skills score a job and never hide it | PRD 1.21 | 0 | feature |
| JOBS-4 | Unfollowing closes a company's postings and deletes nothing; following again reopens the same rows | PRD 1.20 | 0 | feature |
| JOBS-5 | A job description is read by the model once, and only when someone following that company wants that role | v0.19.0 | 1 | cost |
| JOBS-6 | Each account's daily search runs in its own time zone | T6, IDEA-4 | 2 | feature |
| JOBS-7 | One search at a time per account; another account's search never holds it up | IDEA-2 | 2 | performance |
| JOBS-8 | A board fetched recently for anyone is reused instead of fetched again | IDEA-3 | 2 | cost |

## Applying

| Id | Requirement | Source | Phase | Kind |
| --- | --- | --- | --- | --- |
| APPLY-1 | Review mode fills the form and stops at Ready for review; facts only the person knows go to Needs input | v0.7.0 | 0 | feature |
| APPLY-2 | Submit is pressed at most once; an unconfirmed press becomes Failed with "Submit outcome unknown", and Applied is frozen | A2 | 0 | security |
| APPLY-3 | With sending off (`JAP_SUBMIT_ENABLED=false`) nothing is ever submitted | AGENTS.md | 0 | security |
| APPLY-4 | Automatic mode applies only at or above the required-skills bar, and waits when the work mode is unclear | A3, A4 | 0 | feature |
| APPLY-5 | A cover letter is written only when the form requires one, claim-checked against the resume | v0.8.0 | 0 | feature |
| APPLY-6 | Submissions are paced: a daily cap and gap per account, a gap per job site across everyone, a service-wide daily ceiling, and Pause sending | IDEA-17 | 4 | performance |
| APPLY-7 | Applications run side by side, one browser per account under a service ceiling | IDEA-15 | 4 | performance |

## Limits, AI budgets and paying

| Id | Requirement | Source | Phase | Kind |
| --- | --- | --- | --- | --- |
| BILL-1 | `limits_for` alone decides an account's limits: the admin's numbers, then the plan, then free; the owner can only lower them | decision 18 | 5a | feature |
| BILL-2 | Three AI budgets: one account at its allowance stops only itself; the service budget and the total stop the rest | decision 17 | 1 | cost |
| BILL-3 | An account's own OpenRouter key is sealed to it, shown only by its last four characters, and its work is billed to it | IDEA-19 | 4 | feature |
| BILL-4 | The payment webhook trusts only a signature over the raw body, applies each delivery once and never backwards, and is 404 with no provider | IDEA-21 | 5a | security |
| BILL-5 | Checkout is a redirect to the provider's page, refused while not selling; a failed renewal keeps the plan to period end plus grace | IDEA-21 | 5a | feature |

## Running it

| Id | Requirement | Source | Phase | Kind |
| --- | --- | --- | --- | --- |
| OPS-1 | The API process does not mount `/api/inngest`; only the worker does, and it refuses proxied requests | decision 15 | 3 | security |
| OPS-2 | `/api/health` reports each dependency, and health alerts are sent once, not on every check | v0.1.0 onward | 0 | operations |
| OPS-3 | The database is dumped daily and dumps are kept 14 days; dumps are encrypted before they leave the server | IDEA-11 | 3 | operations |
| OPS-4 | A version tag is the release: CI builds the images, live builds nothing, rollback is the previous tag | decision 16 | 3 | operations |
| OPS-5 | The version comes from git tags only, read at build time; no file holds it | decision 25 | 6 | operations |
