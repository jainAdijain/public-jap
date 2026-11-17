"""This month's model spend against its limits (PRD S12).

There are three. Each user has their own monthly allowance (pipeline/limits.py), spent by
the work done for them: parsing their resume, drafting answers, writing letters. Reading
shared job postings is nobody's in particular, so it has the service's own budget
(JAP_SYSTEM_COST_CAP_USD). And everything together stops at JAP_TOTAL_COST_CAP_USD, which
is what bounds the OpenRouter bill. One user reaching their allowance stops only them.
"""

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import tenant
from database.models import LlmCall
from pipeline.limits import limits_for
from schemas.profile import Spend


def month_start(tz: str) -> datetime:
    now = datetime.now(ZoneInfo(tz))
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _spend(spent: float, cap: float) -> Spend:
    return Spend(month_usd=round(spent, 4), cap_usd=cap, reached=spent >= cap)


async def month_spend(session: AsyncSession, user_id: uuid.UUID | None = None) -> Spend:
    """One user's spend this month against their allowance: the session's user unless named."""
    uid = user_id or tenant.user_id(session)
    total = await session.scalar(
        select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(
            LlmCall.user_id == uid, LlmCall.created_at >= month_start(get_settings().jap_tz)
        )
    )
    return _spend(float(total or 0), (await limits_for(session, uid)).ai_usd)


async def system_spend(session: AsyncSession) -> Spend:
    """What reading shared postings cost this month, against the service's budget. Read
    through jap_spend(), which any tenant may call: it returns a sum, never a row."""
    settings = get_settings()
    total = await session.scalar(
        text("SELECT jap_spend(:since, true)"), {"since": month_start(settings.jap_tz)}
    )
    return _spend(float(total or 0), settings.jap_system_cost_cap_usd)


async def total_spend(session: AsyncSession) -> Spend:
    """Everything this month, every user and the service, against the total limit."""
    settings = get_settings()
    total = await session.scalar(
        text("SELECT jap_spend(:since, false)"), {"since": month_start(settings.jap_tz)}
    )
    return _spend(float(total or 0), settings.jap_total_cost_cap_usd)
