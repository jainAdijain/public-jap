# State

**As of 26 Nov 2026.** Open phase: **6, public launch** ([plan.md](intent/plan.md)).
Dev (local) runs `main` at **v0.25.1**. Prod (live) has **never been released**; `v0.25.2` is the
first tag pushed, rehearsed on the VM. The first release will run on Aditya's ARM VM beside the private JAP (decision 31). Update this page whenever something merges, is released, or is found broken.

v0.24.0 (PR 6, `launch`) brought encrypted backups (IDEA-11: `JAP_BACKUP_AGE_RECIPIENT` pipes
`pg_dump` through `age`, `make restore KEY=...` takes the private key); a real terms of service
and privacy notice, Razorpay named, each claim a test in `test_privacy_claims.py`, with a draft
banner until the four operator facts in `frontend/src/legal.ts` are filled in (`TERMS_VERSION`
2026-11-22); `make load-test` (the machine copes with 150 accounts, the AI money does not, see
[launch-plan.md](intent/launch-plan.md), decision D1); `/api/health` telling a stranger only up
or down; dependencies locked and hashed (`make lock`, `cryptography` 50); and pip-audit, npm
audit, gitleaks, Trivy and Dependabot in CI, with the image patched and shipped without pip so
the scan starts green.

v0.25.0 (PR 20) builds Aditya's stage 2 launch
decisions ([launch-plan.md](intent/launch-plan.md), [decisions.md](intent/decisions.md) 27 to
30): **no free AI** (`JAP_MONTHLY_COST_CAP_USD` 0, Plus without its $8; an account's AI is its
own OpenRouter key or credit the admin grants it, and with neither no model is called and the
screens say so), **Automatic mode per account** at the admin's say-so (`0022_launch_switches`),
an **invite list** that lets named addresses sign up while sign-up is paused, and Plus kept off
until real users have tried JAP. New requirements AUTH-11, APPLY-8 and BILL-6, all Proved.
v0.25.1 (PR 21) fixed JAP-2: a spent OpenRouter key stops the work instead of being retried.

## In flight

Every session updates this before it stops: branch, done, half done, next step, blockers.

Stage 3, the first real release. **v0.25.2 is tagged** and the Release workflow pushed both
images for amd64 and arm64 (PR 22: decision 31, live as `public-jap-live` on its own ports,
because the private JAP's live stack runs on the same ARM VM). **It was rehearsed on the VM**
as a non-live stack (`jap-rehearsal`, ports 5393 / 8221 / 8492 / 5437, no submits, no alerts, no
tunnel): images pulled, all six services healthy, `/api/health` green on all four checks,
migrations at `0022`, the admin account created and sign-up paused, a stranger's sign-up
refused with 403, sign-in, Jobs and Settings fine in a browser with no CSP errors. **Restore
drill passed**: an encrypted dump from `pipeline.backup.run_backup`, decrypted and loaded into a
scratch database, identical row counts in all 31 tables, a wrong key refused.

Found and fixed on branch `claude/project-thread-gqxdpu-2` (v0.25.3): the release stack named
`ollama/ollama:0.12.12`, which was never published (now 0.12.11, and CI checks every pulled
image exists for both architectures); `age` was not on the server, which restoring an
encrypted dump there needs; and the first-start steps now say to create the admin and pause
sign-up before the hostname exists.

Next: merge, tag `v0.25.3`, then live. Blocked on Aditya: public-jap's own hostname
(jap.tutorial.monster stays the private JAP's), a Cloudflare tunnel and the secrets in
`.env.live`, and either running the live commands himself or starting the harness with
`JAP_AGENT_ALLOW_LIVE=1`. The rehearsal stack is still running on the VM for him to look at;
`docker compose -p jap-rehearsal ... down -v` in `~/projects/public-jap-rehearsal` removes it.

Still Aditya's: read the final terms wording so `TERMS_VERSION` can be raised once, and move
the PRD files into `docs/intent/sources/`. Plus's prerequisites (postal address, refund page,
GST, Razorpay live) wait until he decides to sell it.

## Never verified for real

What the tests cover is broad (about 440 backend tests, strict mypy, UI lint and build). What
has **never** been exercised end to end, as recorded in the PRs:

| Thing | Status |
| --- | --- |
| Release workflow (`.github/workflows/release.yml`) | Ran for `v0.25.2`: both images, amd64 and arm64, in GHCR |
| `make deploy`, `infra/scripts/server-setup.sh`, the release overlay started for real | Release overlay started for v0.25.2 on the VM as a rehearsal (by hand, not `make deploy`); `make deploy` and `server-setup.sh` never run |
| Content-Security-Policy from Caddy in a real browser | Never checked (sign-up page with Turnstile is the one to check) |
| Google sign-in against Google | Never: no OAuth client yet, tested against a stand-in |
| Razorpay checkout and webhook against Razorpay | Never: tested with Razorpay replaced in memory |
| A real mailbox forward of a Greenhouse code; an inbound-email provider | Never |
| `make capacity` on real applications | Only the empty case |
| The Phase 4 screens in a browser | Not looked at (JAP-7) |
| Restoring a backup | Drilled on the v0.25.2 rehearsal stack (encrypted, 31 tables identical); not yet on live |

Whether a live stack is running today, and on which version, is not visible from the
repository. Day-to-day Docker stacks run on a VM (`make vm-sync`), because the Mac no longer
has the memory (`AGENTS.md`, Stacks on a VM).

## Known broken

From [bugs.md](bugs.md) (read it for the write-ups):

| Id | What | Matters for launch? |
| --- | --- | --- |
| JAP-3 | The skills Matching preset could never match with no required skills | Probably obsolete: skills stopped filtering in v0.18.0. Check and close |
| JAP-4 | `JAP_ENV` still says `dev` and `prod` after the local/live rename | No, cosmetic |
| JAP-7 | The Phase 4 screens have not been looked at | Yes: look before strangers do |

## Open ideas that touch the launch

IDEA-9 (invites, part built in v0.25.0), IDEA-18 (outbound address per user, seam only), IDEA-20
(Automatic mode, decided for the launch), IDEA-25 (a person's own AI subscription), IDEA-26
(AI credit a person can buy), IDEA-22 (browser extension), IDEA-23 (launch review, part built in v0.24.0),
IDEA-24 (Devin cloud blueprint). See [ideas.md](ideas.md).

## Drift between documents

- **The PRD is behind the code** on three points (recorded in PR 2): Google sign-in is built
  (PRD: not now), sign-up is open with an invite list for while it is paused (PRD:
  invite-only at first), and since v0.25.0 there is no free AI allowance at all (PRD: $2). Phase 2 to 5 ids (`S14`, `S15`, `S16`, `T6`, `U22`) were proposed in PR 2 and
  paid plans have no PRD id yet (cited as IDEA-21).
- **`AGENTS.md`** used to say new routers go in the `current_user` loop; the code's loop uses
  `agreed_user` (signed in and agreed to the current notice). Corrected with this folder.
- **Versions** are still edited by hand in four files (`AGENTS.md`, Releases), while decision 25
  says they come from git tags only (OPS-5, a gap). Until that is built, the four-file bump stays
  the rule.
- **Environment names**: the framework says dev and prod; this repo says local and live. They are
  the same two environments ([glossary.md](glossary.md)); `JAP_ENV` still says `dev` and `prod`
  (JAP-4).
- **The private documents** may still sit in `docs/private/` on the Mac; their place is now
  `docs/intent/sources/`. Both are gitignored.
