# Privacy and data

**Requirements:** PRIV-1 to PRIV-3, DATA-1 to DATA-4 ([requirements.md](../intent/requirements.md)). The tests that
prove each one are in [traceability.md](traceability.md); a rule here with no test is a gap
there.

Code: `backend/llm/redact.py`, `backend/llm/call.py`, `backend/observability/scrub.py`,
`backend/pipeline/erasure.py`, `backend/pipeline/retention.py`, `backend/api/account.py`,
`backend/pipeline/storage.py`. Long form: `README.md` (Your data, back or gone; Personal data
and models; Logs, Health and Insights).

## What JAP holds about a person

Name, contact details and links, address, resume versions, saved answers, job preferences,
companies followed, what each job is to them, searches, applications (with the frozen job
description, every answer sent, cover letter, screenshot, trace), AI spend, an optional
OpenRouter key (sealed), and a subscription if they pay.

## What reaches a model

Everything goes through `llm.call()`, which redacts, guards, enforces the spend caps, logs an
`llm_call` row with its cost, and validates the reply against a Pydantic model.

- Names, emails, phones, addresses and profile links become `[NAME]`, `[EMAIL]` and the like;
  a guard refuses any request that still carries one. Cover letters sign off as `[NAME]` and
  the name is put back locally.
- Contact fields on forms are filled by code, never by a model.
- Shared job descriptions are redacted generically and use no user's details.
- `backend/llm/client.py` is the only file that talks to OpenRouter.
- Prompts are versioned files `backend/llm/prompts/<name>.v<N>.md`, each with a `max_tokens`
  cap in `llm/call.py`.

## What reaches the logs

One JSON line per event, scrubbed first: emails, phone numbers, keys, tokens,
`password=...` and security codes replaced; answers, resume text and cover letters never
written. Warnings, errors and apply steps go to `log_event` for 30 days; route timings to
`route_stat`. Sentry, when set, gets errors only, without bodies, cookies, headers or locals.

## Getting it back, and deleting it (S15)

Both in Settings > Account, and both work for an account that has **not** agreed to the
current notice.

- **Download my data**: a zip with one JSON file per private table, every file they point at,
  and a README. A test walks `PRIVATE_TABLES` so a new table cannot be missed. Security codes
  are left out.
- **Delete my account**: password, two-step code when on, and the address typed out. Every
  private row and file goes at once, no undo; a live Razorpay mandate is cancelled first. The
  erasure transaction is the one place an Applied application may be deleted (`SET LOCAL`
  flag, `0016_erasure`). A company nobody follows any more is switched off.
- **What outlives a deletion**, and the notice says so: `llm_call` rows (cost only, user
  nulled, `billed_to='deleted'`) and `log_event` lines (scrubbed, user nulled, gone after 30
  days).

## Retention

| What | Kept |
| --- | --- |
| Traces | `JAP_KEEP_TRACES_DAYS` (30) |
| Screenshots | `JAP_KEEP_SCREENSHOTS_DAYS` (180) |
| `log_event` rows | 30 days |
| Daily database dumps | 14 days on the server |
| Resumes, cover letters, applications | until the user deletes them or their account |

The sweep runs hourly in `health-watch` and removes files only, because an Applied
application is frozen.

## Files

Written under `data/users/<user id>/` (local `data/`, live `data-live/`), served only through
the row that owns them. Files from before tenancy keep their old paths.

## The notice

The tick at sign-up stores `TERMS_VERSION` (2026-11-22). Since v0.24.0 the page
(`frontend/src/pages/LegalPage.tsx`) is a terms of service and a privacy notice covering GDPR
Article 13 and the DPDP Act. It names every party data reaches, Razorpay included for those who
pay for Plus (sent only an account id), makes each claim a test
(`backend/tests/test_privacy_claims.py`), and shows a draft banner whenever any operator fact in
`frontend/src/legal.ts` is a placeholder. They are filled in: the controller is Aditya, in
Rajasthan, India, reached at privacy@tutorial.monster (also the `security.txt` contact), under
the law of India. The address is the state only until a full postal address goes in before Plus
is sold.
