## What changed and why

<!-- The behaviour, the reason, and the ids: requirement (AUTH-3), PRD, JAP-N, IDEA-N. -->

## Version bump

<!-- AGENTS.md, Releases. On a branch that shares the queue with other agents, propose it here
     and leave the four version lines alone. -->

- [ ] patch  - [ ] minor  - [ ] major  - [ ] none (docs, tests or tooling only)
- Release note: <one line>

## Docs (AGENTS.md, Definition of done)

- [ ] `docs/` updated in this PR: which pages, or "no change" with the reason (refactor, tests only)
- [ ] `docs/behaviour/traceability.md` points at the tests that prove what changed; a new
      requirement is in `docs/intent/requirements.md` and a phase in `docs/intent/plan.md`
- [ ] `docs/state.md` says what this PR built, fixed or found, and its "In flight" note is current
- [ ] `docs/intent/decisions.md` has a row if this PR chose between real alternatives
- [ ] `docs/intent/launch-plan.md` ticked or extended if a launch step moved

## Checks

- [ ] `make test` (or `make test-host`) green, including `infra/scripts/check_docs.py`
- [ ] Not verified, and why:
