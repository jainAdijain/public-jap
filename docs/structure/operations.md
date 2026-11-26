# Operations: environments, releases, backups, monitoring

Long form: `README.md` (Hosting: local and live; Make targets), `AGENTS.md` (Running and
verifying; Stacks on a VM). Never write a machine's address, a key or a password into this
folder.

## Environments

| | local | live |
| --- | --- | --- |
| Where | the dev stack (on the Mac, or on the VM via `make vm-sync`) | one Linux server or VM, always on |
| Address | http://localhost:5173 | https://jap.tutorial.monster, through a Cloudflare Tunnel |
| Env file | `.env` | `.env.live`, on the server only |
| Code | the working copy | the images a `v*` tag built, from GHCR |
| Submits / sends email | **never** (`JAP_SUBMIT_ENABLED=false`) | yes, for real |
| Data folder | `data/` | `data-live/` |
| Adminer | yes | no |

Every `make` target takes `ENV=local|live`; leave it off unless the task is hosting. Agents
never run anything with `ENV=live` unless asked. A worktree (`make worktree NAME=...`) is a
separate local stack with its own project, database, ports and `data/`, and never submits or
alerts. A cloud sandbox with no Docker uses `make setup-host` and `make test-host`.

## Checks

`make test` (or `make test-host`) is the definition of done: ruff, ruff format, strict mypy,
pytest (database tests fail rather than skip without Postgres), UI lint and build, agent
guards. CI (`.github/workflows/ci.yml`) runs the same plus `docker compose config -q` for both
compose files, pip-audit, npm audit, gitleaks, and a Trivy scan of the app image (HIGH and
CRITICAL with a fix fail it). The image takes Ubuntu's security updates when it is built and
ships without pip, whose vendored libraries trail their fixes; Dependabot opens the updates.

## Releasing and deploying

**Versions** (decision 25): two environments, dev (local) and prod (live), and the version comes
from git tags only. Patch is a fix; minor a feature, setting or backwards-compatible migration;
major breaks a contract, needs an irreversible migration or a manual step. Before 1.0 minor also
carries breaking changes, and **1.0.0 is the first public launch**. A dev build is named from the
last tag (`0.23.1-dev.7+g<sha>`); prod runs only a `vX.Y.Z` tag. Each PR states its bump and one
release-notes line; the release takes the largest. A migration must work with the version before
it, so rollback stays one command. **Not built yet (OPS-5):** until it is, the version is still
edited in the four places `AGENTS.md` lists.


A version tag is the release.

1. Merge to `main` with `make test` green and the version bumped in the four places
   (`AGENTS.md`, Releases).
2. `git tag vX.Y.Z && git push origin vX.Y.Z`; `.github/workflows/release.yml` builds
   `public-jap-app` and `public-jap-ui` for `linux/amd64` and `linux/arm64`, each on a runner
   of that architecture, and pushes them to GHCR under one tag (decision 31).
3. If the release has a migration, back up live first (`make backup ENV=live` on the server).
4. On the server: `cd /srv/public-jap && make deploy ENV=live TAG=vX.Y.Z`. It refuses a dirty
   checkout, checks the tag out, pulls, starts, waits for `/api/health` and migrates.
5. **Rollback** is the same command with the previous tag; restore a dump too if a migration
   changed data.

**No tag has been pushed yet**, so steps 2 and 4 have never run ([state.md](../state.md)).

### First-time server setup

`infra/scripts/server-setup.sh` (Docker, a `jap` user, firewall open to SSH only, unattended
upgrades, swap, clone to `/srv/public-jap`); a Cloudflare Tunnel of the server's own;
`make env ENV=live` writes `.env.live` with fresh database and Inngest keys; then the secrets
by hand (`TUNNEL_TOKEN`, `OPENROUTER_API_KEY`, `SMTP_PASSWORD`, optional `SENTRY_DSN`). Add
the public hostname in Cloudflare **last**, with a rate-limit rule on `/api/auth/*`.

**On a shared machine** (the first server is an ARM VM that also runs the private JAP's
live stack and dev stacks) skip `server-setup.sh`: its firewall and SSH changes reach
everything on the machine. Clone to `/srv/public-jap` as the login user instead; that
checkout is live's alone, never the `make vm-sync` mirror, because `make deploy` refuses
a dirty checkout and checks tags out. live is Docker project `public-jap-live` on ports
5383 / 8211 / 8491 / 5436, clear of the private JAP's `jap-live` on 5373 / 8201 / 8490 /
5434: one project name would be one database volume (decision 31). Install `age` on it
(`server-setup.sh` does), since `make restore KEY=...` decrypts where it runs.

**The very first start** has an empty database, and sign-up is open until an admin
pauses it. Create the admin account (only `JAP_ADMIN_EMAIL` can be the first) over an SSH
tunnel to the UI port and pause sign-up **before** the public hostname is added.

**Rehearse a release** on a non-live stack before live takes it: a checkout of the tag
with live's settings but its own project name and ports, `JAP_SUBMIT_ENABLED=false`,
`JAP_ALERTS_ENABLED=false` and `cloudflared` scaled to 0. v0.25.2 was rehearsed that way
on the VM (state.md), which is how the missing Ollama tag was found.

`infra/env/live.env` already carries the non-secret live settings: `JAP_SUBMIT_ENABLED`,
`JAP_COOKIE_SECURE=true`, `JAP_UI_URL`, `CORS_ORIGINS`,
`JAP_CLIENT_IP_HEADER=CF-Connecting-IP`, the release images and ports. Still to be set in
`.env.live` (check each with a set/empty test, never print it): `JAP_ADMIN_EMAIL` (so nobody
else can become the first account), `JAP_SECRET_KEY`, the mailbox (`SMTP_*`, `IMAP_*`), and
as wanted Turnstile, Google, `JAP_INBOUND_SECRET`, Razorpay, and
`JAP_BACKUP_AGE_RECIPIENT`.

## Backups

| Copy | What | Where |
| --- | --- | --- |
| Provider disk backups | the whole disk, rolling | the hosting account |
| `daily-backup` | `pg_dump`, first time each day, 14 kept | `data-live/backups/` on the server |
| `make backup-pull SERVER=...` | the newest dump | the Mac, `data/backups-live/` |

The third is the only copy that survives losing the hosting account. `make restore FILE=...`
brings one back. Dumps are encrypted with `age` (`JAP_BACKUP_AGE_RECIPIENT`, public key
on the server, `make restore KEY=...` with the private one). A backup that has never been
restored is not a backup: do a restore drill before launch and then on a schedule.

The drill: make an encrypted dump with the app's own `pipeline.backup.run_backup`,
decrypt it with the private key, load it into a scratch database (`createdb jap_restore`
in the db container), compare every table's row count with the database it came from,
check a wrong key is refused, and drop the scratch database. It passed on the v0.25.2
rehearsal (31 tables identical); on live it is still to do.

## Monitoring

- `/api/health`: API, database, Inngest, Ollama. Answers 200 with `"ok":false` when a
  dependency is unwell. A stranger gets only up or down; the detail is for the admin and
  for callers inside the stack.
- An **uptime check from outside** on `https://<host>/api/health` every 5 minutes matching
  `"ok":true` (Better Stack, UptimeRobot). Every in-stack alert comes from `health-watch`,
  which cannot report the stack being down.
- Settings > Health and System status (admin): daily search, stuck searches, errors, slow
  pages, disk, alerts sent, backups, form tests, capacity, per-site outcomes.
- `make errors`, `make logs`, `make capacity`, `make health-check`, `make tunnel-status`.
- Optional Sentry.

## Incidents

The breach runbook is in README (If something leaks). In short: pause sign-up and set
`JAP_SUBMIT_ENABLED=false` (or `make stop ENV=live`, which takes the tunnel down); back up
before changing anything; work out whose data from `PRIVATE_TABLES`; tell the regulator and
the people affected (GDPR: 72 hours; DPDP: the Board and the users); `make sign-out-all`,
rotate the OpenRouter key, mailbox password, `JAP_SECRET_KEY` and tunnel token; write it up.
