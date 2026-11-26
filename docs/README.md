# public-jap docs

The one place any coding harness (Claude Code, Devin, Codex, Copilot, Cursor, Gemini) or person
reads to learn what public-jap is for, where it stands, how it is built and how it behaves. It is
tracked, public and kept in step with the code in every pull request. The rules for working with
it are in [`AGENTS.md`](../AGENTS.md) (How work moves).

The folder has three parts that must always agree, tied together by requirement ids:

- **Intent**: what we are building and why. Changes least, and wins.
- **Structure**: how it is built, as the code is today.
- **Behaviour**: what it does, each rule proved by named tests.

`infra/scripts/check_docs.py` (in `make test-agents` and CI) fails when they come apart.

## Read in this order

| Page | Answers |
| --- | --- |
| [state.md](state.md) | Where we are: open phase, versions, in flight (the handoff note), known broken, drift |
| [intent/goal.md](intent/goal.md) | Why public-jap exists, for whom, principles that must not break, out of scope |
| [intent/requirements.md](intent/requirements.md) | Every requirement, by area id (`AUTH-1`, `TEN-2`, ...) |
| [intent/plan.md](intent/plan.md) | The phases, what each delivers, and which one is open |
| [intent/launch-plan.md](intent/launch-plan.md) | The steps of Phase 6, from today to open sign-up |
| [intent/decisions.md](intent/decisions.md) | Decisions taken and why; the ones still open |
| [structure/architecture.md](structure/architecture.md) | Services, diagrams, code layout, data model, how requests and jobs move |
| [structure/operations.md](structure/operations.md) | local and live, releasing, deploying, backups, monitoring, incidents |
| [behaviour/traceability.md](behaviour/traceability.md) | Requirement to code to tests, for every requirement |
| [behaviour/auth-and-isolation.md](behaviour/auth-and-isolation.md) | Accounts, sessions, consent, route classes, the admin, isolation |
| [behaviour/plans-and-limits.md](behaviour/plans-and-limits.md) | Free and Plus, `limits_for`, the three AI budgets, billing |
| [behaviour/jobs-pipeline.md](behaviour/jobs-pipeline.md) | Fetching, dedup, matching, searches, applying, security codes |
| [behaviour/privacy-and-data.md](behaviour/privacy-and-data.md) | What reaches a model and the logs; export, erasure, retention |
| [glossary.md](glossary.md) | Words that mean one thing here |
| [ideas.md](ideas.md) | `IDEA-N`: how public-jap may evolve; not planned until moved into the plan |
| [bugs.md](bugs.md) | `JAP-N`: known bugs and issues |

## Which document wins

1. **The PRD**, with the build plan and design doc under it. They are Aditya's private product
   documents and carry personal data, so they live in `docs/intent/sources/` on his machine and
   are never committed (`.gitignore`, `infra/scripts/check-tracked.sh`). Never copy their
   contents into a tracked file. [requirements.md](intent/requirements.md) is their public
   summary.
2. **`docs/intent/`**, then **`docs/structure/`** and **`docs/behaviour/`**.
3. **The code.** Code that disagrees with intent is a bug, unless intent is changed first, on
   purpose.

Where two of them disagree, fix the lower one in the same pull request, or record the drift in
[state.md](state.md) when only Aditya can decide. **`README.md`** at the root is the long-form user
and operator guide (every screen, setting and `make` target); these pages summarise and point at
it. Its Version history table is the changelog.

Style: plain sentences, present tense, file paths in backticks, ids where they exist. No secrets,
no machine addresses, no personal data: this repo is public.
