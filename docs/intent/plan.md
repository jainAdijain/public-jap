# Plan

public-jap goes from single-user JAP to a public service one phase at a time. **One phase is
open at a time.** Each phase names its goal, the requirements it delivers (ids from
[requirements.md](requirements.md)), the test that closes it, and its status: planned, open or
done.

- A phase opens when the one before it is done, or when Aditya says to start it.
- A phase closes when its exit test passes: every requirement it delivers is Proved in
  [traceability.md](../behaviour/traceability.md) or an accepted gap, [state.md](../state.md) is
  updated, and Aditya agrees.
- An idea that comes up mid-phase goes to [ideas.md](../ideas.md). It joins a phase only when
  Aditya moves it here.
- A phase may be split (5a, 5b) when part of it waits on evidence.

Phases 1 to 5a were built before this page existed; their exit tests are written after the fact
from what each PR proved.

### Phase 0: Single-user JAP (done)
Goal: one person's job search, from following companies to submitting applications, on one Mac.
Built in v0.1.0 to v0.18.x, before the fork. (Single-user JAP had its own build-plan phases 0 to 8;
they are not these.)
Delivers: AUTH-2, AUTH-4, AUTH-5, AUTH-6, AUTH-8, PRIV-1, PRIV-2, PRIV-3, JOBS-1, JOBS-2, JOBS-3, JOBS-4, APPLY-1, APPLY-2, APPLY-3, APPLY-4, APPLY-5, OPS-2
Exit test: all Proved.

### Phase 1: Each user's own data (done)
Goal: open sign-up, every account a private workspace, per-account AI limits (PRD 1.18 step 2).
Built in v0.19.0, PR 1.
Delivers: AUTH-1, AUTH-3, AUTH-10, TEN-1, TEN-2, TEN-3, TEN-4, TEN-5, JOBS-5, BILL-2
Exit test: all Proved; `test_tenancy.py` makes a second user try every route.

### Phase 2: Consent, your data, searches side by side (done)
Goal: nothing done without agreement, data back or gone, accounts not waiting on each other.
Built in v0.20.0, PR 2.
Delivers: AUTH-7, CONSENT-1, CONSENT-2, CONSENT-3, DATA-1, DATA-2, JOBS-6, JOBS-7, JOBS-8
Exit test: all Proved.

### Phase 3: Live on a server (done)
Goal: live runs on one Linux server behind a Cloudflare Tunnel, released by tag (PRD H1 to H8).
Built in v0.21.0, PR 3.
Delivers: AUTH-9, TEN-8, DATA-3, OPS-1, OPS-3, OPS-4
Exit test: all Proved or accepted gaps. OPS-4 (release by tag) has never run: it is closed by
Phase 6, stage 3.

### Phase 4: Applying at scale (done)
Goal: many accounts applying side by side, paced, each with its own security-code address and
optionally its own key.
Built in v0.22.0, PR 4.
Delivers: TEN-6, TEN-7, APPLY-6, APPLY-7, BILL-3
Exit test: all Proved. The screens were never looked at by hand (JAP-7).

### Phase 5a: Plus, a paid plan (done)
Goal: a plan that can be paid for through Razorpay, behind `limits_for` (IDEA-21).
Built in v0.23.0, PR 5.
Delivers: BILL-1, BILL-4, BILL-5
Exit test: all Proved. Never run against Razorpay itself.

### Phase 5b: Submitting from the user's machine (planned)
Goal: a browser extension that submits from the user's own browser (IDEA-22), only if job sites
block server-side submission.
Delivers: nothing yet; requirements are written when Aditya moves IDEA-22 here.
Exit test: to be written with the requirements.

### Phase 6: Public launch (open)
Goal: strangers can sign up, and 1.0.0 is tagged. The steps, with Aditya's decisions marked, are in
[launch-plan.md](launch-plan.md).
In PR 6 (`launch`, v0.24.0): encrypted backups, the real terms and privacy notice, load test,
supply-chain checks.
Delivers: DATA-4, OPS-5
Exit test: [launch-plan.md](launch-plan.md) stage 6 done, DATA-4 and OPS-5 Proved, OPS-4 run for
real, `v1.0.0` tagged and running in prod.
