"""`make load-test`: does one server hold 150 people applying for jobs? (IDEA-23)

The launch review has to answer three questions and write the answers down — how many
users the server holds, what breaks first, and what it costs a month — because a number
nobody wrote down is a number nobody acts on at 2am. This measures the first two and
computes the third.

It is a measurement, not a test, which is why it lives next to capacity.py rather than in
tests/: it prints a report and only fails when a target is missed. `make capacity` is its
other half, and reads real applications; this one builds a synthetic world, because the
only way to see 150 accounts searching at once is to make 150 accounts.

**It builds its own database** (`<db>_load`, dropped and recreated each run) and never
touches the one the stack is using. Nothing here reaches OpenRouter, Ollama or a job site:
no model is called, no board is fetched, and no application is submitted, so a run costs
nothing and cannot be seen from outside.

What it measures, and the bottleneck each one stands for:

- **matching**: the whole-catalogue filter every search ends with, for every account. The
  per-search database cost, and the one that grows with users times listings.
- **the pages a signed-in user loads**, with many of them at once: whether the API stays
  usable while the searches run.
- **the connection pool** against the server's max_connections, counting both app and
  worker processes and the browsers Phase 4 added.
- **disk**, from the screenshots and traces an application leaves and how long retention
  keeps them.
- **money**, from the per-user AI cap and the service's own budget.

What it does not measure, and why: embedding rate (needs the server's CPU and Ollama, so
`make ollama-check` and a real run are the measurement), and how long one application
holds a browser — that is `make capacity`, from applications that really happened. Both
are fed in here as assumptions, and the report says they are assumptions.
"""

import asyncio
import os
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parent.parent

# The launch target, agreed 19 Nov 2026: about 150 people sending 20 applications a month.
USERS = 150
APPLICATIONS_PER_USER_PER_MONTH = 20
# Boards and jobs are shared, so the catalogue does not grow with users. 150 people
# following a company each, with the overlap real users have, is a few hundred boards.
BOARDS = 300
LISTINGS_PER_BOARD = 40

# What "survives" means. Agreed before measuring, so the numbers below are a verdict and
# not a shrug.
MATCH_BUDGET_S = 60.0 * 60  # every account's search done inside an hour
PAGE_P95_MS = 500.0  # a signed-in page, while that is going on
CONNECTION_HEADROOM = 0.8  # of Postgres max_connections

# Assumptions, named as such in the report. Replace with measurements from the server.
SECONDS_PER_APPLICATION = 120.0  # `make capacity` on real applications
SCREENSHOT_MB = 0.4
TRACE_MB = 6.0


@dataclass
class Result:
    name: str
    value: float
    unit: str
    budget: float | None = None
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.budget is None or self.value <= self.budget

    def line(self) -> str:
        mark = "    " if self.budget is None else ("ok  " if self.ok else "OVER")
        against = f" (budget {self.budget:,.0f} {self.unit})" if self.budget is not None else ""
        return f"  {mark} {self.name:<44}{self.value:>12,.1f} {self.unit}{against}  {self.note}"


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def add(self, *args: object, **kwargs: object) -> None:
        self.results.append(Result(*args, **kwargs))  # type: ignore[arg-type]

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def _load_url() -> str:
    from config import get_settings

    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}_load").render_as_string(hide_password=False)


def build_database() -> str:
    """A scratch database at the current migration, dropped first. Never the live one."""
    from alembic import command
    from alembic.config import Config

    from config import get_settings

    url = _load_url()
    name = make_url(url).database
    assert name and name.endswith("_load")
    admin = make_url(url).set(database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(_libpq(admin), autocommit=True, connect_timeout=5) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "database" / "migrations"))
    command.upgrade(cfg, "head")
    return url


def build_world(url: str, users: int, boards: int, per_board: int) -> None:
    """The synthetic world, in bulk SQL: accounts with profiles, a shared catalogue of
    companies, boards, listings and postings, and a follow from each account.

    Written as the table owner, naming every user_id, because this is the service setting
    up rather than one account acting (database/tenant.py).
    """
    from auth.consent import TERMS_VERSION

    with psycopg.connect(_libpq(url), autocommit=True) as conn:
        # The notice as it stands, or every one of these accounts is bounced to the
        # agreement screen and the pages measure nothing (PRD S14).
        conn.execute(
            """
            INSERT INTO app_user (email, password_hash, email_verified_at, terms_accepted_at,
                                  terms_version)
            SELECT 'load' || i || '@example.test', 'x', now(), now(), %s
            FROM generate_series(1, %s) AS i
            """,
            (TERMS_VERSION, users),
        )
        conn.execute(
            """
            INSERT INTO profile (user_id, full_name, target_roles, locations)
            SELECT id, 'Load User', ARRAY['engineer', 'developer'], ARRAY['Remote']
            FROM app_user WHERE email LIKE 'load%@example.test'
            """
        )
        conn.execute(
            """
            INSERT INTO company (name, canonical_name)
            SELECT 'Company ' || i, 'company ' || i FROM generate_series(1, %s) i
            """,
            (boards,),
        )
        conn.execute(
            """
            INSERT INTO watched_board (company_id, ats, board_token, display_name, origin,
                                       active)
            SELECT id, 'greenhouse', 'tok' || id, name, 'seed', true FROM company
            """
        )
        conn.execute(
            """
            -- One listing per company and role: a listing IS the deduplicated job, and
            -- the database holds it to that, so the roles have to differ.
            INSERT INTO listing (company_id, canonical_role, title_display)
            SELECT c.id, 'engineer ' || g, 'Senior Engineer ' || g
            FROM company c, generate_series(1, %s) g
            """,
            (per_board,),
        )
        conn.execute(
            """
            INSERT INTO posting (watched_board_id, listing_id, source, external_id,
                                 company_name_raw, title_raw, posting_url, apply_url,
                                 description_html, description_text, description_hash,
                                 raw_payload, state, first_seen_at, last_seen_at)
            SELECT b.id, l.id, 'greenhouse', 'ext-' || l.id, c.name, l.title_display,
                   'https://example.test/' || l.id, 'https://example.test/apply/' || l.id,
                   '<p>A job</p>', 'A job', md5(l.id::text), '{}'::jsonb, 'open', now(), now()
            FROM listing l
            JOIN company c ON c.id = l.company_id
            JOIN watched_board b ON b.company_id = l.company_id
            """
        )
        # Each account follows a slice of the boards, overlapping the way real ones do.
        conn.execute(
            """
            INSERT INTO board_follow (user_id, board_id, active)
            SELECT u.id, b.id, true
            FROM (SELECT id, row_number() OVER (ORDER BY id) AS n FROM app_user
                  WHERE email LIKE 'load%@example.test') u
            JOIN (SELECT id, row_number() OVER (ORDER BY id) AS n FROM watched_board) b
              ON b.n BETWEEN u.n AND u.n + 1
            ON CONFLICT DO NOTHING
            """
        )


def measure_matching(url: str, report: Report, users: int) -> None:
    """The filter every search ends with, run for every account, as the pipeline runs it.

    This is the query whose cost is users times listings, so it is the first thing that
    stops a 06:00 where everybody searches at once.
    """
    with psycopg.connect(_libpq(url), autocommit=True) as conn:
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM app_user WHERE email LIKE 'load%@example.test' ORDER BY id"
            ).fetchall()
        ]
        listings = conn.execute("SELECT count(*) FROM listing").fetchone()
        taken: list[float] = []
        for user_id in ids:
            start = time.perf_counter()
            conn.execute(f"SET app.user_id = '{user_id}'")
            conn.execute(
                """
                INSERT INTO user_listing (user_id, listing_id, matched, matched_role)
                SELECT %s, l.id, '{role}', 'engineer'
                FROM listing l
                WHERE EXISTS (
                  SELECT 1 FROM posting p
                  JOIN board_follow f ON f.board_id = p.watched_board_id
                  WHERE p.listing_id = l.id AND p.state = 'open'
                    AND f.user_id = %s AND f.active
                )
                ON CONFLICT (user_id, listing_id) DO UPDATE SET matched = EXCLUDED.matched
                """,
                (user_id, user_id),
            )
            taken.append(time.perf_counter() - start)
    total = sum(taken)
    report.add(
        "one account's match over the catalogue",
        statistics.median(taken) * 1000,
        "ms",
        None,
        f"{listings[0] if listings else 0:,} listings",
    )
    report.add(
        f"all {users} accounts, one after another",
        total,
        "s",
        MATCH_BUDGET_S,
        "the 06:00 herd, with no parallelism at all",
    )


async def measure_pages(url: str, report: Report, concurrency: int = 20) -> None:
    """The pages a signed-in user loads, many at once, against the real app.

    Through ASGI rather than a socket: this is the application's own time, without a
    local network or a dev server's reload watcher in the number.
    """
    import httpx

    from auth import sessions
    from database.session import get_engine, get_sessionmaker
    from main import app

    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    with psycopg.connect(_libpq(url), autocommit=True) as conn:
        row = conn.execute(
            "SELECT id FROM app_user WHERE email LIKE 'load%@example.test' LIMIT 1"
        ).fetchone()
        assert row is not None
        user_id = uuid.UUID(str(row[0]))
        token = "load-test-token"
        conn.execute(
            "INSERT INTO auth_session (user_id, token_hash, stage, expires_at, last_seen_at)"
            " VALUES (%s, %s, 'signed_in', now() + interval '1 day', now())",
            (user_id, sessions.token_hash(token)),
        )

    cookie = {sessions.cookie_name(sessions.SIGNED_IN): token}
    paths = ["/api/listings?status=not_applied", "/api/profile", "/api/runs", "/api/boards"]
    taken: list[float] = []

    async def hit(client: httpx.AsyncClient, path: str) -> None:
        start = time.perf_counter()
        resp = await client.get(path, cookies=cookie)
        taken.append((time.perf_counter() - start) * 1000)
        assert resp.status_code in (200, 404), f"{path}: {resp.status_code} {resp.text[:200]}"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://load"
    ) as client:
        await asyncio.gather(*(hit(client, paths[0]) for _ in range(3)))  # warm
        taken.clear()
        for _ in range(5):
            await asyncio.gather(*(hit(client, paths[i % len(paths)]) for i in range(concurrency)))
    taken.sort()
    p95 = taken[int(len(taken) * 0.95) - 1]
    report.add("a signed-in page, median", statistics.median(taken), "ms")
    report.add(
        "a signed-in page, 95th", p95, "ms", PAGE_P95_MS, f"{concurrency} at once, warm cache"
    )
    await get_engine().dispose()


def measure_limits(url: str, report: Report, users: int) -> None:
    """Connections, disk and money: arithmetic over the settings, not a measurement, but
    the three ceilings that are hit without anything looking slow first."""
    from config import get_settings

    settings = get_settings()
    with psycopg.connect(_libpq(url), autocommit=True) as conn:
        row = conn.execute("SHOW max_connections").fetchone()
        max_conn = int(row[0]) if row else 100
    # SQLAlchemy's default pool is 5 with 10 of overflow, per process: app and worker.
    per_process = 15
    processes = 2
    report.add(
        "database connections at full stretch",
        per_process * processes,
        "conns",
        max_conn * CONNECTION_HEADROOM,
        f"max_connections is {max_conn}",
    )

    per_day = users * APPLICATIONS_PER_USER_PER_MONTH / 30
    browser_hours = per_day * SECONDS_PER_APPLICATION / 3600
    report.add(
        "browser-hours of applying a day",
        browser_hours,
        "h",
        24 * settings.jap_apply_browsers,
        f"{per_day:,.0f} a day at an assumed {SECONDS_PER_APPLICATION:.0f}s each, "
        f"{settings.jap_apply_browsers} browsers",
    )
    report.add(
        "applications a day against the service ceiling",
        per_day,
        "a day",
        float(settings.jap_daily_applications_total),
        "JAP_DAILY_APPLICATIONS_TOTAL",
    )

    kept = per_day * (
        SCREENSHOT_MB * settings.jap_keep_screenshots_days
        + TRACE_MB * settings.jap_keep_traces_days
    )
    report.add(
        "disk held by screenshots and traces",
        kept / 1024,
        "GB",
        None,
        f"at retention of {settings.jap_keep_screenshots_days} and "
        f"{settings.jap_keep_traces_days} days",
    )
    report.add(
        "AI spend a month if every account uses its cap",
        users * settings.jap_monthly_cost_cap_usd + settings.jap_system_cost_cap_usd,
        "usd",
        float(settings.jap_total_cost_cap_usd),
        "JAP_TOTAL_COST_CAP_USD is the stop",
    )


def statement(report: Report, users: int) -> list[str]:
    over = [r for r in report.results if not r.ok]
    out = [
        "",
        f"Capacity at {users} accounts sending {APPLICATIONS_PER_USER_PER_MONTH} applications"
        " a month",
        "=" * 78,
    ]
    out += [r.line() for r in report.results]
    out += ["", "What breaks first:"]
    if over:
        out += [
            f"  {r.name} — {r.value:,.1f} {r.unit} against a budget of {r.budget:,.0f}"
            for r in over
        ]
        out.append("")
        out.append("Raise the ceiling, add a worker, or hold sign-up at a number this fits.")
    else:
        out.append("  Nothing measured here is over its budget at this size.")
    out += [
        "",
        "Assumed, not measured here: one application holds a browser for "
        f"{SECONDS_PER_APPLICATION:.0f}s (use `make capacity` on real applications), and "
        "embedding runs at the server's CPU speed (`make ollama-check`).",
        "The job boards are not part of this run: no board was fetched and nothing was sent.",
    ]
    return out


def run(users: int = USERS) -> tuple[Report, list[str]]:
    """Synchronous on purpose: building the database runs Alembic, whose env.py calls
    asyncio.run() itself and so cannot be inside a loop. Only the page measurement is
    async, and it gets a loop of its own."""
    report = Report()
    url = build_database()
    build_world(url, users, BOARDS, LISTINGS_PER_BOARD)
    measure_matching(url, report, users)
    asyncio.run(measure_pages(url, report))
    measure_limits(url, report, users)
    return report, statement(report, users)


def main() -> int:
    users = int(sys.argv[1]) if len(sys.argv) > 1 else USERS
    # Thousands of request lines would bury the report, and writing them to log_event
    # would measure the logger as much as the route. Set before anything reads settings.
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    os.environ.setdefault("JAP_LOG_DB", "false")

    from config import get_settings

    if get_settings().jap_env == "prod":
        print("Refusing to run against prod: this drops and rebuilds a database.")
        return 2
    report, lines = run(users)
    print("\n".join(lines))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
