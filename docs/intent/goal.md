# Goal

## What public-jap is

JAP (Job Application Pipeline) watches the public job boards of companies a person follows
(Greenhouse, Lever and Ashby), lists the jobs that fit their profile, and prepares or submits
applications for them to review.

JAP began as a personal, single-user tool on one Mac. **public-jap is the fork that turns it
into a public service**: anyone can sign up, and every account is a private workspace whose
data no other account can see or change.

## The goal

Open JAP to the public safely. In order of importance:

1. **Each user's data is theirs alone.** Profile, resume, answers, companies followed,
   searches, applications, files and AI spend belong to one account. What one user does
   never changes another user's list, and nothing one user owns is ever shown to another,
   including the admin. Enforced twice: in the application and in Postgres row-level security
   ([auth-and-isolation.md](../behaviour/auth-and-isolation.md)).
2. **Many people can sign up and use it at once** without waiting on each other: open sign-up
   with email confirmation or Google, searches and applications side by side per account,
   pacing across the whole service so job sites see a well-behaved client.
3. **Nothing is done in a person's name without their agreement.** Terms and privacy notice
   at sign-up, a separate opt-in before JAP applies for them, and another before it applies
   without asking first.
4. **The operator can afford it and run it.** Per-account limits and AI budgets so one person
   cannot run up the bill, a paid plan for those who want more, one server that releases by
   tag, encrypted backups, monitoring from outside.
5. **Honest about what it is.** The notice says plainly what JAP stores, what goes to a model
   and what applying in someone's name means; every claim it makes is a test.

## Who it is for

- **Job seekers** who want the boards of companies they care about watched, and applications
  prepared from their own resume and saved answers, with them in the loop.
- **The operator** (Aditya), who is the admin: runs the server, sees counts and health, never
  user content.

## Principles that must not be broken

These are enforced by tests. Breaking one is a bug even when everything else passes.

- No personal data reaches a model: names, emails, phones, addresses and profile links are
  redacted, and a guard refuses any request that still carries one (`backend/llm/redact.py`).
- `backend/llm/client.py` is the only code that talks to OpenRouter (`tests/test_isolation.py`).
- Prompts are versioned files; changing behaviour means a new version.
- Submit is pressed at most once; an unknown outcome becomes Failed, never a second press.
- Every `/api` route needs a session except a small, listed open set (`tests/test_auth.py`).
- Logs are scrubbed; answers, resume text and letters are never logged.
- Secrets never enter the repo, the output or a model.

The full list, with the reasons, is in `AGENTS.md` (Rules that must not be broken).

## What is out of scope for now

- Submitting from the user's own browser (IDEA-22), an outbound address per user (IDEA-18,
  seam only), invites (IDEA-9). Each waits on evidence that it is needed.
- Job sites other than Greenhouse, Lever and Ashby.
- Selling outside India: no merchant of record chosen yet (IDEA-21).
