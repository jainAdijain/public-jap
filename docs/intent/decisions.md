# Decisions

Taken decisions, so they are not re-argued, and the ones still open. Add a row when a pull
request chooses between real alternatives. Newest at the bottom of each table. The full
reasoning usually sits in the PR, `README.md` version history or [ideas.md](../ideas.md).

## Taken

| # | Decision | Why | Where |
| --- | --- | --- | --- |
| 1 | Job facts are shared, everything about a person is private | a company followed by many is fetched and read once; each person's list stays theirs | v0.19.0, `0011_tenancy` |
| 2 | Isolation is checked twice: in the app and by Postgres row-level security | a query that forgets its filter must return nothing, not someone else's rows | `0013_rls`, `database/tenant.py` |
| 3 | Another user's id answers 404, never 403 | a 403 confirms the id exists | `tests/test_tenancy.py` |
| 4 | Open sign-up with email confirmation, not invite-only | the admin can pause sign-up and turn accounts off; invites (IDEA-9) wait for need. **The PRD still says invite-only at first** | v0.19.0 |
| 5 | The first account is the admin; `JAP_ADMIN_EMAIL` pins it | a fresh JAP needs an owner without a CLI step | v0.13.0 |
| 6 | The admin sees counts, dates, addresses and health, never user content; no Adminer on live | the operator must not be able to read resumes and answers from the UI | IDEA-12, v0.21.0 |
| 7 | Three separate consents (terms; apply in my name; apply without asking) and the version agreed is stored | three different things to agree to; a migration never writes a consent | S14, v0.20.0 |
| 8 | Export and delete work without agreeing to the notice | refusing the notice must not trap a person | S15, v0.20.0 |
| 9 | Delete is immediate, no grace period; `llm_call` and `log_event` survive with the user nulled | erasure means erasure; the bill and the service record carry no personal content | `0016_erasure` |
| 10 | No personal data to any model; contact fields filled by code | the model is a third party | `llm/redact.py` |
| 11 | One file talks to OpenRouter | one place to redact, guard, cap and log | `llm/client.py`, `test_isolation.py` |
| 12 | Submit is pressed at most once; unknown outcome is Failed | a duplicate application in someone's name is worse than asking them to check | A2 |
| 13 | Skills never hide a job; they score it | a filter on skills hid good jobs whose description listed none | v0.18.0, PRD 1.21 |
| 14 | live is one server behind a Cloudflare Tunnel, no inbound port | nothing to attack but SSH; one machine is enough for a long time | v0.21.0, H1 to H8 |
| 15 | `app` and `worker` are one image, `JAP_ROLE` apart; `/api/inngest` is absent from `app` | the endpoint that runs functions is not reachable from a browser at all | v0.21.0 |
| 16 | Release by tag; live builds nothing | what runs, the compose files and the migrations are always one version; rollback is one command | v0.21.0 |
| 17 | Three AI budgets: per account, the service's for shared reading, and a total | one person reaching theirs stops only them; the total bounds the bill | v0.19.0, `llm/spend.py` |
| 18 | `limits_for` is the only place that decides limits; order admin > plan > free, owner can only lower | billing must not leak into the rest of the code | v0.23.0 |
| 19 | Checkout is a redirect to a page Razorpay hosts; the webhook trusts only a signature | no card data, no third-party script, CSP untouched | v0.23.0 |
| 20 | A failed payment keeps the plan to period end plus grace | the provider is still retrying; Indian e-mandates give notice before debit | v0.23.0 |
| 21 | Security codes matched by recipient address, one per account | "newest code in the mailbox" would cross accounts once applications run side by side | v0.22.0, IDEA-16 |
| 22 | An outbound proxy per user is a seam only (`JAP_APPLY_PROXY`) | costs money per account; build it when the per-site refusal rates say so | IDEA-18 |
| 23 | Full-disk encryption dropped; dumps encrypted with `age` instead (PR 6) | the key would have to live on an unattended machine | IDEA-11 |
| 24 | Product documents (PRD etc.) stay private in `docs/intent/sources/`; `docs/` is the public, tracked description | the repo is public and the PRD carries personal data; harnesses still need one readable source | `docs/`, `.gitignore` |
| 25 | Versions come from git tags only; two environments, dev (local) and prod (live); 1.0.0 is the first public launch | one place for the number ends the forgotten fourth file and the clash between parallel branches; a tag is already the release (16) | OPS-5, [plan.md](plan.md) Phase 6 |
| 26 | `docs/` follows the project docs framework: intent, structure, behaviour, requirement ids by area, one open phase, a drift check in CI | any harness can pick the work up; code, tests and docs can be checked against each other | `AGENTS.md`, `infra/scripts/check_docs.py` |

| 27 | No free AI: an account's AI runs on its own OpenRouter key or on credit the admin grants it (D1) | 150 free accounts at $1 could spend $155 against a $20 cap; this way the bill is only what the admin chose to give. Signing in with a user's Claude or ChatGPT subscription was ruled out: those terms do not allow a third-party service to use them | v0.25.0, `pipeline/limits.py`, IDEA-25 |
| 28 | Automatic mode is the admin's to allow, account by account; everyone else is on Review (D2) | one server address for everyone: one account's automatic run getting JAP flagged (Ashby has bot detection) costs every account that site; a wrong submission cannot be taken back | v0.25.0, IDEA-20 |
| 29 | Plus stays off until real users have tried JAP; when it comes back it sells companies and applications, not AI (D3) | Razorpay activation, a postal address, a refund page and the tax position are all still to do, and nobody yet knows whether 1 and 1 a day is too little | v0.25.0, IDEA-21 |
| 30 | The soft launch admits people by an invite list while sign-up stays paused (D4); this replaces decision 4's open sign-up for now | a leaked link lets nobody in, unlike opening sign-up for a window; the same per-account control as D1 and D2 | v0.25.0, IDEA-9 |
## Open (owner's call)

| # | Question | Notes | Where |
| --- | --- | --- | --- |
| D5 | The operator facts and legal wording of the notice | name, postal address, contact, governing law | PR 6, `frontend/src/legal.ts` |
