# public-jap docs

This folder is the one place any coding harness (Claude Code, Devin, Codex, Copilot, Cursor,
Gemini) or person reads to learn what public-jap is for, what state it is in, how it is built,
how it behaves and what happens next. It is tracked, public and kept in step with the code.

**Last checked against the code:** `main` at v0.23.0 (26 Nov 2026). PR 6 (`launch`, v0.24.0)
is open and not merged; where it changes something, the page says so.

## Read in this order

| File | What it answers |
| --- | --- |
| [goal.md](goal.md) | Why public-jap exists, who it is for, and the principles nothing may break |
| [current-state.md](current-state.md) | What is built, what is in flight, what is known broken, what has never been tried for real |
| [architecture.md](architecture.md) | The services, the code layout, the data model (shared vs private) and how a request and a background job move |
| [auth-and-isolation.md](auth-and-isolation.md) | Accounts, sessions, consent, the route classes, the admin, and how one user's data is kept from another's |
| [plans-and-limits.md](plans-and-limits.md) | Free and Plus, `limits_for`, the three AI budgets, billing through Razorpay |
| [jobs-pipeline.md](jobs-pipeline.md) | Fetching boards, one row per job, matching, searches, applying, Automatic mode, security codes, cover letters |
| [privacy-and-data.md](privacy-and-data.md) | What reaches a model, what reaches the logs, export, erasure, retention, files |
| [operations.md](operations.md) | local and live, releasing, deploying, backups, monitoring, incidents |
| [decisions.md](decisions.md) | The decisions already taken and why, so they are not re-argued; and the ones still open |
| [launch-plan.md](launch-plan.md) | The steps from today to open sign-up, with the owner's decisions marked |

## Which document wins

1. **The PRD**, the build plan and the design doc are Aditya's private product documents. They
   carry personal data, so they live in `docs/private/` on his machine and are never committed
   (`.gitignore`, `infra/scripts/check-tracked.sh`). Never copy their contents into this folder.
2. **This folder** describes the product as it is meant to be and as the code actually is.
   Where it and the PRD disagree, say so in [current-state.md](current-state.md) (PRD drift)
   rather than quietly following either.
3. **`README.md`** at the root is the long-form user and operator guide: every screen, every
   setting, every `make` target. These pages summarise and point at it; they do not repeat
   every detail.
4. **`AGENTS.md`** holds the rules for coding agents. **`BUGS.md`** and **`IDEAS.md`** are the
   numbered defect and idea lists; these pages link to their ids rather than copying them.

When the code and a page here disagree, one of them is a bug. Fix whichever is wrong in the
same pull request, and never leave the page describing behaviour that no longer exists.

## Keeping it current (every pull request)

A change that alters intent, structure or behaviour updates this folder **in the same pull
request**. Concretely:

- a new or changed route, setting, table, Inngest function, plan, limit or screen: the page
  that covers it ([architecture.md](architecture.md) for structure, the behaviour page for
  what it does);
- something built, merged, released or found broken: [current-state.md](current-state.md);
- a choice made between real alternatives: a row in [decisions.md](decisions.md);
- a launch step done or a new one found: tick or add it in [launch-plan.md](launch-plan.md);
- bump the "Last checked against the code" line above when you have re-read the pages
  against the code.

A pure refactor with no change in behaviour, or a test-only change, needs no edit here; say
so in the PR ("Docs: no change, refactor only"). The PR template asks.

Style: plain sentences, present tense, file paths in backticks, ids (`PRD S14`, `IDEA-21`,
`JAP-2`) where they exist. No secrets, no addresses of machines, no personal data: this repo is
public.
