"""What starts a run besides the Run dialog (T1, T2, Triggers): a profile save and the
Daily run. An accepted resume starts its run from the API (api/resumes.py).

profile-saved: the save has already re-filtered the stored listings (T2, in PUT
/api/profile); this fetches in the background so new matching jobs follow. Debounced, so a
burst of saves makes one run, and a run already queued is reused (T4).

Both are per user (PRD S13). profile-saved carries the user who saved and is debounced per
user. daily-schedule looks at every active account's Daily run and queues a run for each
one that is due, in that user's time zone and with that user's mode.

daily-schedule: ticks every minute in JAP_TZ and fires when the Daily run is on and its
time (06:00 unless changed) has come. A tick is due only in the first few minutes after
that time, so a slot missed while the Mac slept is skipped, not caught up (accepted for v1).
In Review mode it finds jobs only; in Automatic mode it finds and applies (Phase 8), within
the daily cap, so the morning run applies without me.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import inngest
from sqlalchemy import select

from config import get_settings
from database import tenant
from database.models import AppUser, Profile
from database.session import get_sessionmaker
from functions.client import inngest_client
from pipeline.runs import queue_run

DUE_WINDOW = timedelta(minutes=5)


def daily_due(now: datetime, at: time, last_fired: datetime | None) -> bool:
    """Whether the Daily run fires now: within DUE_WINDOW after today's slot (in now's
    zone), and not fired since that slot."""
    slot = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if not slot <= now < slot + DUE_WINDOW:
        return False
    return last_fired is None or last_fired < slot


async def queue_step(trigger: str) -> list[dict[str, str]]:
    """Queue a find only run for the event's user; the run when it is new and needs its
    event sent."""
    async with get_sessionmaker()() as session:
        run, created = await queue_run(session, trigger, "find_only")
    return [{"run_id": str(run.id), "user_id": str(run.user_id)}] if created else []


async def due_users() -> list[tuple[str, str]]:
    """The accounts whose Daily run is due now, each with the mode to run in. Read as the
    service; an account that is turned off or not yet confirmed never runs (S12)."""
    out: list[tuple[str, str]] = []
    with tenant.as_system():
        async with get_sessionmaker()() as session:
            profiles = await session.scalars(
                select(Profile)
                .join(AppUser, AppUser.id == Profile.user_id)
                .where(
                    Profile.daily_run,
                    AppUser.disabled_at.is_(None),
                    AppUser.email_verified_at.is_not(None),
                )
            )
            for profile in profiles:
                tz = ZoneInfo(profile.tz or get_settings().jap_tz)
                now = datetime.now(tz)
                last = (
                    profile.daily_run_last_at.astimezone(tz) if profile.daily_run_last_at else None
                )
                if not daily_due(now, profile.daily_run_at, last):
                    continue
                profile.daily_run_last_at = datetime.now(UTC)
                mode = "find_and_apply" if profile.apply_mode == "automatic" else "find_only"
                out.append((str(profile.user_id), mode))
            await session.commit()
    return out


async def daily_step() -> list[dict[str, str]]:
    """Fire the Daily run for every user it is due for: the new runs to send, each with its
    user."""
    runs: list[dict[str, str]] = []
    for user_id, mode in await due_users():
        with tenant.as_user(user_id):
            async with get_sessionmaker()() as session:
                run, created = await queue_run(session, "scheduled", mode)
        if created:
            runs.append({"run_id": str(run.id), "user_id": user_id})
    return runs


async def _send(ctx: inngest.Context, runs: list[dict[str, str]]) -> list[str]:
    if runs:
        await ctx.step.send_event(
            "send-run", [inngest.Event(name="run/requested", data=run) for run in runs]
        )
    return [run["run_id"] for run in runs]


@inngest_client.create_function(
    fn_id="profile-saved",
    trigger=inngest.TriggerEvent(event="profile/saved"),
    debounce=inngest.Debounce(period=timedelta(seconds=2), key="event.data.user_id"),
    retries=2,
)
async def profile_saved(ctx: inngest.Context) -> list[str]:
    return await _send(ctx, await ctx.step.run("queue-run", queue_step, "profile_saved"))


@inngest_client.create_function(
    fn_id="daily-schedule",
    trigger=inngest.TriggerCron(cron=f"TZ={get_settings().jap_tz} * * * * *"),
    retries=0,  # the next tick is a minute away
)
async def daily_schedule(ctx: inngest.Context) -> list[str]:
    return await _send(ctx, await ctx.step.run("check", daily_step))
