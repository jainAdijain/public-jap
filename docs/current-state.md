# Current state

**As of 26 Nov 2026.** `main` is **v0.23.0**. Update this page whenever something merges, is
released, or is found broken.

## Where the code is

The plan has six phases, from single-user JAP to a public service. Phases are the "Phase"
column of `IDEAS.md`.

| Phase | What | State |
| --- | --- | --- |
| 1 | Each user's own data, open sign-up, per-user AI limits (PRD 1.18 step 2) | Merged, v0.19.0 (PR 1) |
| 2 | Consent, export and delete, searches side by side, time zones, Google sign-in (S14, S15, S16, T6, U22) | Merged, v0.20.0 (PR 2) |
| 3 | live on one Linux server behind a Cloudflare Tunnel, release by tag, hardening, retention (H1 to H8) | Merged, v0.21.0 (PR 3) |
| 4 | Applying at scale: side by side, pacing, a security-code address per user, your own OpenRouter key | Merged, v0.22.0 (PR 4) |
| 5a | Plus, a paid plan through Razorpay (IDEA-21) | Merged, v0.23.0 (PR 5) |
| 5b | Browser extension that submits from the user's machine (IDEA-22) | Not started; only if blocking numbers call for it |
| 6 | The public launch review (IDEA-23) | **In PR 6**, not merged |

### In flight: PR 6, branch `launch`, v0.24.0

Not on `main` yet. When it merges, move these lines into the table above and update the pages
it touches ([privacy-and-data.md](privacy-and-data.md), [operations.md](operations.md)).

- Encrypted backups (IDEA-11): `JAP_BACKUP_AGE_RECIPIENT` pipes `pg_dump` through `age`; the
  server holds only the public key, `make restore KEY=...` takes the private one.
- A real terms of service and privacy notice (`frontend/src/pages/LegalPage.tsx`,
  `frontend/src/legal.ts`), each claim backed by `backend/tests/test_privacy_claims.py`.
  It shows a draft banner until the four operator facts (name, postal address, contact
  address, governing law) are filled in. `TERMS_VERSION` moves to 2026-11-22.
- `make load-test` (150 accounts): the machine copes; the AI money does not (see
  [launch-plan.md](launch-plan.md), decision D1).
- `/api/health` tells a stranger only up or down; dependencies pinned and hashed in
  `backend/requirements.lock` (`make lock`); pip-audit, npm audit, gitleaks, Trivy and
  Dependabot in CI; `/.well-known/security.txt`; a breach runbook in README.
- Still needs Aditya: the operator facts, and the AI-budget decision.

## Has it run for real?

What the tests cover is broad (about 440 backend tests, strict mypy, UI lint and build). What
has **never** been exercised end to end, as recorded in the PRs:

| Thing | Status |
| --- | --- |
| Release workflow (`.github/workflows/release.yml`) | Never run: **no `v*` tag has been pushed**, so no image is in GHCR |
| `make deploy`, `infra/scripts/server-setup.sh`, the release overlay started for real | Never run on a server |
| Content-Security-Policy from Caddy in a real browser | Never checked (sign-up page with Turnstile is the one to check) |
| Google sign-in against Google | Never: no OAuth client yet, tested against a stand-in |
| Razorpay checkout and webhook against Razorpay | Never: tested with Razorpay replaced in memory |
| A real mailbox forward of a Greenhouse code; an inbound-email provider | Never |
| `make capacity` on real applications | Only the empty case |
| The Phase 4 screens in a browser | Not looked at (JAP-7) |
| Restoring a backup | Never recorded |

Whether a live stack is running today, and on which version, is not visible from the
repository. Day-to-day Docker stacks run on a VM (`make vm-sync`), because the Mac no longer
has the memory (`AGENTS.md`, Stacks on a VM).

## Known defects

From `BUGS.md` (read it for the write-ups):

| Id | What | Matters for launch? |
| --- | --- | --- |
| JAP-2 | A spent OpenRouter key is retried until it fills the log | Yes: with one shared key, a spent key would flood every account's work |
| JAP-3 | The skills Matching preset could never match with no required skills | Probably obsolete: skills stopped filtering in v0.18.0. Check and close |
| JAP-4 | `JAP_ENV` still says `dev` and `prod` after the local/live rename | No, cosmetic |
| JAP-7 | The Phase 4 screens have not been looked at | Yes: look before strangers do |

## Open ideas that touch the launch

IDEA-9 (invites), IDEA-18 (outbound address per user, seam only), IDEA-20 (whether Automatic
mode is offered to the public), IDEA-22 (browser extension), IDEA-23 (launch review, in PR 6),
IDEA-24 (Devin cloud blueprint). See `IDEAS.md`.

## Drift between documents

- **The PRD is behind the code** on three points (recorded in PR 2): Google sign-in is built
  (PRD: not now), sign-up is open (PRD: invite-only at first), the free AI allowance is $1
  (PRD: $2). Phase 2 to 5 ids (`S14`, `S15`, `S16`, `T6`, `U22`) were proposed in PR 2 and
  paid plans have no PRD id yet (cited as IDEA-21).
- **README** says v0.22.0 in its header while the code says 0.23.0; PR 6 fixes it.
- **`AGENTS.md`** used to say new routers go in the `current_user` loop; the code's loop uses
  `agreed_user` (signed in and agreed to the current notice). Corrected with this folder.
