"""How long one application takes, and what that says about running many at once (IDEA-15).

Applying holds a browser: read-form opens one, and so does the submit that presses the
button. While it is held, that worker is busy. So the number that decides how many users
can apply side by side is not wall-clock time from Started to Applied, which includes a
person reading and approving, but the time the steps themselves run.

Nothing new is recorded for this. The Inngest middleware already writes one log_event per
step with its duration (observability/inngest_mw.py), so this only adds them up:

- per step, how many ran and how long they took (median, 90th, worst);
- per application, the total of its steps, which is how long it occupies a worker;
- from the median of that, applications per browser-hour, which times the number of
  browsers is the service's capacity.

Steps are grouped by name with the pass number taken off, so submit-0 and submit-1 are both
"submit". A submit that is only waiting for the daily cap or the 30-second gap returns
before it opens a browser, so "submit" mixes cheap checks with real submissions and its
median reads low; the per-application total is the honest figure and the one to plan with.

log_event is kept 30 days (observability/alerts.py), so that is as far back as this can see.
"""

import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.ops import Capacity, SiteRow, StepTime

# Every apply-listing step with a duration, in the window. Shared by both queries below so
# they always describe the same rows.
STEPS = """
    SELECT application_id,
           regexp_replace(fields->>'step', '-[0-9]+$', '') AS step,
           (fields->>'duration_ms')::numeric AS ms
    FROM log_event
    WHERE created_at >= :since
      AND event = 'inngest.step'
      AND fields->>'function' = 'apply-listing'
      AND fields->>'step' IS NOT NULL
      AND fields->>'duration_ms' IS NOT NULL
      AND application_id IS NOT NULL
"""

BY_STEP_SQL = f"""
WITH s AS ({STEPS})
SELECT step, count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY ms) AS median,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY ms) AS p90,
       max(ms) AS worst
FROM s GROUP BY step ORDER BY median DESC
"""

PER_APPLICATION_SQL = f"""
WITH s AS ({STEPS}), t AS (
    SELECT application_id, sum(ms) AS ms FROM s GROUP BY 1
)
SELECT count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY ms) AS median,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY ms) AS p90,
       max(ms) AS worst
FROM t
"""

# IDEA-17: what each job site did with the submissions sent to it. A site that starts
# rejecting, or asking for a code every time, is a site that has decided the service looks
# like a bot — which is the signal for an outbound address per user (IDEA-18).
SITES_SQL = """
SELECT fields->>'ats' AS ats, count(*) AS n,
       count(*) FILTER (WHERE (fields->>'confirmed')::boolean) AS confirmed,
       count(*) FILTER (WHERE (fields->>'rejected')::boolean) AS rejected,
       count(*) FILTER (WHERE (fields->>'needs_code')::boolean) AS needs_code
FROM log_event
WHERE created_at >= :since AND event = 'apply.outcome' AND fields->>'ats' IS NOT NULL
GROUP BY 1 ORDER BY n DESC
"""

DEFAULT_DAYS = 30


@dataclass(frozen=True)
class Window:
    days: int
    since: datetime


def window(days: int = DEFAULT_DAYS, now: datetime | None = None) -> Window:
    now = now or datetime.now(UTC)
    return Window(days=days, since=now - timedelta(days=days))


def _ms(value: object) -> int | None:
    return None if value is None else int(float(str(value)))


async def capacity(
    session: AsyncSession, days: int = DEFAULT_DAYS, now: datetime | None = None
) -> Capacity:
    w = window(days, now)
    steps = [
        StepTime(
            step=str(r.step),
            n=int(r.n),
            median_ms=_ms(r.median) or 0,
            p90_ms=_ms(r.p90) or 0,
            worst_ms=_ms(r.worst) or 0,
        )
        for r in await session.execute(text(BY_STEP_SQL), {"since": w.since})
    ]
    sites = [
        SiteRow(
            ats=str(r.ats),
            submissions=int(r.n),
            confirmed=int(r.confirmed),
            rejected=int(r.rejected),
            needs_code=int(r.needs_code),
        )
        for r in await session.execute(text(SITES_SQL), {"since": w.since})
    ]
    total = (await session.execute(text(PER_APPLICATION_SQL), {"since": w.since})).one()
    median = _ms(total.median)
    # One browser does an hour's worth of applications of the median length; the service's
    # capacity is this times however many browsers run at once.
    per_hour = round(3_600_000 / median, 1) if median else None
    return Capacity(
        days=w.days,
        applications=int(total.n),
        steps=steps,
        sites=sites,
        median_ms=median,
        p90_ms=_ms(total.p90),
        worst_ms=_ms(total.worst),
        per_browser_hour=per_hour,
    )


def _s(ms: int | None) -> str:
    return "-" if ms is None else f"{ms / 1000:.1f} s"


def lines(c: Capacity) -> list[str]:
    """The report as `make capacity` prints it."""
    if not c.applications:
        return [
            f"No apply steps in the last {c.days} days, so there is nothing to measure yet.",
            "Apply to something (or wait for a daily run) and ask again.",
        ]
    out = [
        f"{c.applications} applications in the last {c.days} days.",
        "",
        f"  {'step':<22}{'n':>5}{'median':>10}{'90th':>10}{'worst':>10}",
    ]
    out += [
        f"  {s.step:<22}{s.n:>5}{_s(s.median_ms):>10}{_s(s.p90_ms):>10}{_s(s.worst_ms):>10}"
        for s in c.steps
    ]
    out += [
        "",
        f"  {'per application':<22}{c.applications:>5}"
        f"{_s(c.median_ms):>10}{_s(c.p90_ms):>10}{_s(c.worst_ms):>10}",
        "",
        f"One browser gets through about {c.per_browser_hour} applications an hour.",
        "Capacity is that times the browsers running at once.",
    ]
    if c.sites:
        out += ["", f"  {'site':<14}{'sent':>7}{'confirmed':>11}{'refused':>9}{'code':>7}"]
        out += [
            f"  {s.ats:<14}{s.submissions:>7}{s.confirmed:>11}{s.rejected:>9}{s.needs_code:>7}"
            for s in c.sites
        ]
        out.append("Refusals or codes climbing on a site means it is reading JAP as a bot.")
    return out


async def report(days: int = DEFAULT_DAYS) -> str:
    from database import tenant
    from database.session import get_sessionmaker

    with tenant.as_system():
        async with get_sessionmaker()() as session:
            return "\n".join(lines(await capacity(session, days)))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.isdigit()]
    print(asyncio.run(report(int(args[0]) if args else DEFAULT_DAYS)))
