"""What one account may use (PRD S12): AI spend a month, companies followed, applications
a day. limits_for() is the only place that decides, so a paid plan later changes one
function.

Today there is one plan, free, with its numbers in .env. The admin can give any one account
its own number for each of the three (Settings > Accounts; app_user.limit_*), and is not
held to the plan themselves. Inside those, a user can lower their own AI limit and daily cap
in Settings, never raise them.
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
    # The account's plan, before what its owner chose for themselves inside it.
    plan: str = "Free"
    plan_ai_usd: float = 0.0
    plan_applications: int = 0
    custom: bool = False  # the admin set at least one number for this account


async def limits_for(session: AsyncSession, user_id: uuid.UUID) -> Limits:
    settings = get_settings()
    account = await session.get(AppUser, user_id)
    profile = await profile_of(session, user_id)
    admin = account is not None and account.is_admin
    set_ai = float(account.limit_ai_usd) if account and account.limit_ai_usd is not None else None
    set_boards = account.limit_boards if account else None
    set_apps = account.limit_applications if account else None

    plan_ai = set_ai if set_ai is not None else settings.jap_monthly_cost_cap_usd
    plan_apps = (
        set_apps
        if set_apps is not None
        else ADMIN_DAILY_APPLICATIONS
        if admin
        else settings.jap_free_daily_applications
    )
    boards = set_boards if set_boards is not None else None if admin else settings.jap_free_boards

    own_ai = float(profile.spend_cap_usd) if profile and profile.spend_cap_usd is not None else None
    own_cap = profile.daily_cap if profile else None
    if own_ai is None:
        ai = plan_ai
    elif admin and set_ai is None:  # the admin's own setting stands as it is
        ai = own_ai
    else:
        ai = min(own_ai, plan_ai)
    return Limits(
        ai_usd=ai,
        boards=boards,
        applications_per_day=min(own_cap, plan_apps) if own_cap is not None else plan_apps,
        plan="Admin" if admin else "Free",
        plan_ai_usd=plan_ai,
        plan_applications=plan_apps,
        custom=any(v is not None for v in (set_ai, set_boards, set_apps)),
    )
