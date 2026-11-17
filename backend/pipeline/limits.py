"""What one account may use (PRD S12): AI spend a month, companies followed, applications
a day. limits_for() is the only place that decides, so a paid plan later changes one
function. Today there is one plan, free, set in .env; the admin is not held to it.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import AppUser
from pipeline.workspace import profile_of

ADMIN_DAILY_APPLICATIONS = 100  # the most profile.daily_cap can hold


@dataclass(frozen=True)
class Limits:
    ai_usd: float  # model spend a month on JAP's key
    boards: int | None  # companies followed; None: no limit
    applications_per_day: int


async def limits_for(session: AsyncSession, user_id: uuid.UUID) -> Limits:
    """The account's limits, with what its owner chose for themselves inside them: a
    monthly AI limit and a daily cap set in Settings can lower the plan's, never raise it
    (the admin's own settings stand as they are)."""
    settings = get_settings()
    account = await session.get(AppUser, user_id)
    profile = await profile_of(session, user_id)
    admin = account is not None and account.is_admin
    own_ai = float(profile.spend_cap_usd) if profile and profile.spend_cap_usd is not None else None
    own_cap = profile.daily_cap if profile else None
    plan_ai = settings.jap_monthly_cost_cap_usd
    plan_cap = ADMIN_DAILY_APPLICATIONS if admin else settings.jap_free_daily_applications
    if admin:
        ai = own_ai if own_ai is not None else plan_ai
    else:
        ai = min(own_ai, plan_ai) if own_ai is not None else plan_ai
    return Limits(
        ai_usd=ai,
        boards=None if admin else settings.jap_free_boards,
        applications_per_day=min(own_cap, plan_cap) if own_cap is not None else min(20, plan_cap),
    )
