"""Health alert emails (PRD O6), checked every hour by health-watch.

An email goes to the admin when:
- someone's Daily search didn't start (half an hour after its time)
- a search has been stuck for over 30 minutes
- more errors than JAP_ALERT_ERRORS_PER_HOUR happened in the last hour
- AI spend reached 80% or 100% of this month's total limit, or reading shared postings
  reached the service's budget
- the disk that holds data/ is more than 85% full
and to a user when their own AI allowance for the month is used up (PRD S12, S13).
Failed searches, failed applications and failed form tests already have their own alerts
(pipeline/alerts.py). Each alert has a key that includes the day (or the month for spend, or
the run for a stuck search), and alert_state remembers it was sent, so one problem sends at
most one email a day. JAP_ALERTS_ENABLED=false (dev, test) sends nothing.
"""

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import AlertState, LlmCall, LogEvent, RateLimit, RouteStat
from llm.spend import month_spend, month_start, system_spend, total_spend
from observability import status as health
from pipeline.notify import EmailNotSent, Mail
from pipeline.workspace import admin_id, contact_email

DISK_LIMIT_PCT = 85.0
KEEP_LOGS = timedelta(days=30)


@dataclass
class Alert:
    key: str
    subject: str
    lines: list[str]
    user_id: str | None = None  # whose alert it is; None: the service's, sent to the admin

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ui() -> str:
    return get_settings().jap_ui_url.rstrip("/") + "/#settings"


async def due(session: AsyncSession, now: datetime | None = None) -> list[Alert]:
    """The alerts that apply now and were not sent yet under their key."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    local = now.astimezone(ZoneInfo(settings.jap_tz))
    day, month = local.strftime("%Y-%m-%d"), local.strftime("%Y-%m")
    found: list[Alert] = []

    missed = await health.missed_daily(session, now)
    if missed:
        found.append(
            Alert(
                f"daily-missed:{day}",
                "JAP alert: today's Daily search didn't start",
                [
                    f"The Daily search of {missed} account{'s' if missed != 1 else ''} was due "
                    "and has not started today.",
                    "The Mac may have been asleep, or Docker or Inngest may not be running.",
                ],
            )
        )
    for run in await health.stuck_runs(session, now):
        minutes = int((now - run.started_at).total_seconds() // 60)
        found.append(
            Alert(
                f"stuck-run:{run.id}",
                "JAP alert: a search is stuck",
                [
                    f"A search started {minutes} minutes ago is still {run.status}.",
                    "Check the Inngest dashboard and the app logs (make logs).",
                ],
            )
        )
    errors = await health.error_count(session, now - timedelta(hours=1))
    if errors > settings.jap_alert_errors_per_hour:
        groups = await health.error_groups(session, now - timedelta(hours=1), limit=5)
        found.append(
            Alert(
                f"errors:{day}",
                f"JAP alert: {errors} errors in the last hour",
                [f"JAP logged {errors} errors in the last hour. The most common:", ""]
                + [f"- {g.event} ({g.count}): {g.last_message[:200]}" for g in groups],
            )
        )
    spend = await total_spend(session)
    pct = 100 * spend.month_usd / spend.cap_usd if spend.cap_usd else 100.0
    if pct >= 100:
        found.append(
            Alert(
                f"spend-100:{month}",
                "JAP alert: the total AI spend limit is reached",
                [
                    f"${spend.month_usd:.2f} of ${spend.cap_usd:.2f} is used this month, across "
                    "every user and the shared job postings.",
                    "JAP has stopped reading descriptions and writing answers and letters for "
                    "everyone until next month or until JAP_TOTAL_COST_CAP_USD is raised.",
                ],
            )
        )
    elif pct >= 80:
        found.append(
            Alert(
                f"spend-80:{month}",
                f"JAP alert: {pct:.0f}% of this month's total AI limit is used",
                [f"${spend.month_usd:.2f} of ${spend.cap_usd:.2f} is used this month."],
            )
        )
    shared = await system_spend(session)
    if shared.reached:
        found.append(
            Alert(
                f"spend-system:{month}",
                "JAP alert: the budget for reading job postings is used up",
                [
                    f"${shared.month_usd:.2f} of ${shared.cap_usd:.2f} is used this month.",
                    "New job descriptions are not being read (skills, work mode) until next "
                    "month or until JAP_SYSTEM_COST_CAP_USD is raised.",
                ],
            )
        )
    spenders = await session.scalars(
        select(LlmCall.user_id)
        .where(LlmCall.user_id.is_not(None), LlmCall.created_at >= month_start(settings.jap_tz))
        .group_by(LlmCall.user_id)
        .having(func.sum(LlmCall.cost_usd) > 0)
    )
    for uid in spenders:
        own = await month_spend(session, uid)
        if own.reached:
            found.append(
                Alert(
                    f"spend-user:{uid}:{month}",
                    "JAP: your AI limit for this month is reached",
                    [
                        f"${own.month_usd:.2f} of ${own.cap_usd:.2f} is used this month.",
                        "JAP has stopped drafting answers and writing cover letters for you "
                        "until next month. Finding jobs carries on.",
                    ],
                    user_id=str(uid),
                )
            )
    space = health.disk()
    if space.used_pct > DISK_LIMIT_PCT:
        found.append(
            Alert(
                f"disk:{day}",
                f"JAP alert: the disk is {space.used_pct:.0f}% full",
                [
                    f"The disk that holds JAP's data is {space.used_pct:.0f}% full "
                    f"({space.free_bytes // 2**30} GB free).",
                    f"data/ uses {space.data_bytes // 2**20} MB, of which backups "
                    f"{space.backups_bytes // 2**20} MB.",
                ],
            )
        )
    if not found:
        return []
    sent = set(
        await session.scalars(
            select(AlertState.key).where(AlertState.key.in_([a.key for a in found]))
        )
    )
    return [a for a in found if a.key not in sent]


async def mail_for(session: AsyncSession, alert: Alert) -> Mail:
    to = alert.user_id or await admin_id(session)
    email = await contact_email(session, uuid.UUID(str(to))) if to else None
    if not email:
        raise EmailNotSent("There is no email address to write to")
    env = get_settings().jap_env
    subject = alert.subject if env == "prod" else f"[{env.upper()}] {alert.subject}"
    body = "\n".join([*alert.lines, "", f"Status in JAP: {_ui()}"]) + "\n"
    return Mail(to=str(email), subject=subject, body=body)


async def already_sent(session: AsyncSession, key: str) -> bool:
    return await session.get(AlertState, key) is not None


async def mark_sent(session: AsyncSession, alert: Alert, now: datetime | None = None) -> None:
    stmt = insert(AlertState).values(
        key=alert.key,
        last_sent_at=now or datetime.now(UTC),
        detail=alert.subject,
        user_id=uuid.UUID(alert.user_id) if alert.user_id else None,
    )
    await session.execute(stmt.on_conflict_do_nothing(index_elements=["key"]))
    await session.commit()


async def cleanup(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Delete log events and route timings past 30 days, and alert keys past 90 (O3)."""
    now = now or datetime.now(UTC)
    logs = await session.execute(delete(LogEvent).where(LogEvent.created_at < now - KEEP_LOGS))
    routes = await session.execute(delete(RouteStat).where(RouteStat.hour < now - KEEP_LOGS))
    keys = await session.execute(
        delete(AlertState).where(AlertState.last_sent_at < now - timedelta(days=90))
    )
    # Sign-up and forgot-password tries (auth/limits.py) are counted per hour; a day is plenty.
    tries = await session.execute(
        delete(RateLimit).where(RateLimit.window_start < now - timedelta(days=1))
    )
    await session.commit()
    return {
        "log_events": int(getattr(logs, "rowcount", 0) or 0),
        "route_stats": int(getattr(routes, "rowcount", 0) or 0),
        "alert_keys": int(getattr(keys, "rowcount", 0) or 0),
        "rate_limits": int(getattr(tries, "rowcount", 0) or 0),
    }
