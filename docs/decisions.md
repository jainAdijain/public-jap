# Decisions

Taken decisions, so they are not re-argued, and the ones still open. Add a row when a pull
request chooses between real alternatives. Newest at the bottom of each table. The full
reasoning usually sits in the PR, `README.md` version history or `IDEAS.md`.

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
| 24 | Product documents (PRD etc.) stay private in `docs/private/`; this folder is the public, tracked description | the repo is public and the PRD carries personal data; harnesses still need one readable source | this folder |

## Open (owner's call)

| # | Question | Notes | Where |
| --- | --- | --- | --- |
| D1 | How the AI budget fits the number of free accounts | $155/month of free allowances at 150 accounts vs a $20 total cap | [launch-plan.md](launch-plan.md) |
| D2 | Is Automatic mode offered to the public? | job-site terms and bot detection; Review mode is human-in-the-loop | IDEA-20 |
| D3 | Selling Plus: when, and with whom outside India | terms, refund page, tax position; merchant of record not chosen | IDEA-21 |
| D4 | Invites, or open sign-up with a pause switch | how the soft launch admits a fixed group | IDEA-9 |
| D5 | The operator facts and legal wording of the notice | name, postal address, contact, governing law | PR 6, `frontend/src/legal.ts` |
