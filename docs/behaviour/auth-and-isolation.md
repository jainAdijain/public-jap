# Accounts, sign-in and data isolation

**Requirements:** AUTH-1 to AUTH-11, TEN-1 to TEN-8, CONSENT-1 to CONSENT-3 ([requirements.md](../intent/requirements.md)). The tests that
prove each one are in [traceability.md](traceability.md); a rule here with no test is a gap
there.

The long-form version is `README.md` (Sign-in and accounts; What you agree to). Code:
`backend/auth/`, `backend/api/auth.py`, `backend/api/account.py`, `backend/database/tenant.py`,
migrations `0007_auth`, `0011_tenancy`, `0012_email_verify`, `0013_rls`, `0015_consent`,
`0017_user_identity`.

## Accounts

- **Anyone can sign up** from the sign-in screen. The first account on a fresh JAP is the
  **admin** and goes straight in; `JAP_ADMIN_EMAIL` restricts that first account to one
  address. Every later account confirms its address by an emailed single-use link (24 hours).
  The answer is the same whether or not the address already exists.
- **Paused, by invitation** (launch decision D4). While the admin has sign-up paused, only an
  address on the admin's invite list (Settings > Accounts, Invite) can create an account, by
  password or by Google; Google must say the address is verified before an invite counts. The
  invite leaves the list once its account exists. The sign-in screen says "New accounts are by
  invitation for now" and keeps the form while the list is not empty. Nothing is emailed: the
  admin tells the person. Inviting an address that already has an account is refused. A
  paused sign-up refuses an uninvited address with `sign_up_closed` and an invited one goes on
  to the emailed link, so trying addresses could tell an invited one from another; the tries
  limit and Turnstile bound that.
- **Sign-up is limited** per visitor address and per email (`JAP_AUTH_TRIES_PER_HOUR_IP`,
  `JAP_AUTH_TRIES_PER_HOUR_EMAIL`, 429 past them). Behind the tunnel
  `JAP_CLIENT_IP_HEADER=CF-Connecting-IP` is required, or every visitor shares one address.
  Turnstile is optional (`TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`).
- **Passwords**: at least 12 characters, salted scrypt; five wrong tries lock sign-in for 15
  minutes. Forgot password emails a 30-minute single-use link to `JAP_UI_URL`.
- **Google sign-in** (S16): OIDC with PKCE, shown only when the three `GOOGLE_*` settings are
  set. An existing account is joined only when Google says the address is verified.
- **Two-step sign-in**: TOTP with replay protection and 8 recovery codes.
- **Sessions**: HttpOnly host-only cookie, 7 days idle, 30 days at most;
  `JAP_COOKIE_SECURE=true` wherever JAP is on HTTPS (live).
- A new account starts empty; nothing of the admin's is copied to it.

## Consent (S14)

Three separate agreements, each stored with what was agreed:

| Agreement | Asked | Without it |
| --- | --- | --- |
| Terms and privacy notice | the tick at sign-up | every `/api` route but health, auth and account answers 403 `terms_not_accepted` |
| JAP may apply in my name | first Apply | nothing is prepared or sent |
| JAP may do that without asking | turning on Automatic mode | Automatic cannot be turned on |

`TERMS_VERSION` in `backend/auth/consent.py` is kept per account; raising it asks everyone
again, so raise it only when what the notice *says* changes. Taking back "may apply" takes
"without asking" with it, and Submit re-reads consent at the last moment.

## Route classes

Every router is registered in `backend/main.py` in exactly one class:

| Class | Dependency | Routers |
| --- | --- | --- |
| Open | none | `/api/health`, `/api/auth/*` |
| Open, own credential | shared secret / HMAC, 404 without it | `/api/inbound/email` (`JAP_INBOUND_SECRET`), `/api/webhooks/{provider}` (signature over the raw body) |
| Signed in | `current_user` | `account` (export, delete): works even without consent, so nobody is trapped |
| Signed in and agreed | `agreed_user` | `profile`, `resumes`, `boards`, `answers`, `runs`, `postings`, `listings`, `applications`, `insights`, `billing` |
| Admin | `admin_user` (404 for anyone else) | `ops`, `duplicates`, `admin`, `account.admin_router` |

`/api/inngest` is mounted only on the worker and answers only direct calls from inside Docker,
never a request with proxy headers. The open set is pinned by `tests/test_auth.py`; adding to
it is a decision, recorded in [decisions.md](../intent/decisions.md).

## How one user's data is kept from another's

Checked twice, on purpose:

1. **In the application.** Every route and background job works for one user. An id that
   belongs to someone else answers 404, the same as one that does not exist.
   `tests/test_tenancy.py` makes a second user try every route that takes an id.
2. **In Postgres.** The app connects as a role that owns nothing (`jap_app`); every
   transaction sets `app.user_id`; row-level security on every table in `PRIVATE_TABLES`
   returns only that user's rows. A query that forgot its filter returns nothing, not someone
   else's data. Only work on shared data (fetching, reading postings, dedup, admin screens,
   webhooks, migrations, make targets) runs as the system tenant.

Other parts of the same rule:

- **Shared facts, private lists.** A company two people follow is fetched once and each
  description read once; each user's own filter makes their own list from the companies they
  follow. Hiding, applying or unfollowing changes only your list. Merge and "Not the same job"
  change shared listings, so they are the admin's.
- **Files** go under `data/users/<user id>/` and are served only through the row that owns
  them.
- **Cross-user aggregates** (pacing per job site, the service-wide daily ceiling) are read
  through SQL functions that return a number, never a row (`jap_last_submit`,
  `jap_applied_today`).
- **Security codes** are matched by the address they were sent to (`jap+<token>@...`, one per
  account), never by "the newest code in the mailbox".

## The admin

The admin (Settings > Accounts) sees each account's address, when it joined and last signed
in, its state and its AI spend this month: **never anything a user wrote**. The admin can turn
an account off (signed out everywhere, work stops, data kept) or on, set one account's own
limits (including AI credit on JAP's key, launch decision D1), allow or stop Automatic mode
for it (D2), pause sign-up and keep the invite list (D4), and delete an account on request.
Each account's row says whose money its AI runs on: its own key, credit, or none yet. System status, Health, backups, form
tests, capacity and duplicates are admin-only. The admin's own account cannot be deleted. On
live there is no Adminer; looking at the database means SSH to the server.

Machine-side tools: `make set-password EMAIL=...`, `make two-step-off EMAIL=...`,
`make sign-out-all EMAIL=...`.
