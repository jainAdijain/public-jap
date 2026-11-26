# Glossary

Words that mean one thing in public-jap. A newcomer, person or harness, would guess some of them
wrong.

| Word | Means |
| --- | --- |
| Account, user | One person's private workspace. Everything about them carries their `user_id` |
| Admin | The first account (or the one `JAP_ADMIN_EMAIL` names). Sees counts and health, never content |
| Tenant | Whose data a database session may see: one user (`app.user_id` set) or the system |
| System tenant | Work on shared data with no user in scope: fetching, dedup, webhooks, migrations |
| Private table | A table in `PRIVATE_TABLES` (`backend/database/tenant.py`): `user_id` plus row-level security |
| Shared table | Facts about jobs, no `user_id`: `company`, `watched_board`, `posting`, `listing` and others |
| Board | One company's job board on Greenhouse, Lever or Ashby (`watched_board`, shared) |
| Follow | A user's link to a board (`board_follow`, private) |
| Posting | One job as one board lists it, under that board's id. Never deleted, only closed with a reason |
| Listing | One job after dedup: the postings of the same company and role grouped together |
| User listing | What a listing is to one user: matched or not, skills match, status (`user_listing`) |
| Run, search | One account's fetch of its boards, then dedup and matching, and optionally applying |
| Daily search | The run each account gets once a day, at its own time in its own time zone |
| Application | One attempt to apply to one listing, from Preparing to Applied, Failed or Cancelled |
| Review mode | JAP fills the form and waits for the person to send it (the default) |
| Automatic mode | JAP sends a filled form without asking, under its own consent and the skills bar |
| Needs input | An application waiting for something only the person can answer |
| Applied | Submitted and confirmed. Frozen: nothing may change it except erasure |
| Security code | The 8-character code Greenhouse may email before taking an application |
| Plan | Free or Plus; decides the limits through `limits_for` |
| Allowance | An account's AI spend a month on JAP's OpenRouter key |
| BYOK | An account's own OpenRouter key, billed to it and outside its allowance |
| local, dev | The development stack (`.env`, `data/`), which never submits. The framework calls it dev |
| live, prod | The server (`.env.live`, `data-live/`), which submits for real. The framework calls it prod |
| PRD id | An id from the private PRD, such as `A2`, `S14`, `H4` |
| Requirement id | An id from [requirements.md](intent/requirements.md), named by area, such as `TEN-2` |
| JAP-N | A numbered bug or issue in [bugs.md](bugs.md) |
| IDEA-N | A numbered idea in [ideas.md](ideas.md) |
| Phase | A step of [plan.md](intent/plan.md); one is open at a time |
