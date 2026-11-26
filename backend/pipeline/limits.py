"""What one account may use (PRD S12) and which plan says so (IDEA-21): AI spend a month,
companies followed, applications a day. limits_for() is the only place that decides, so the
rest of the code — api/boards.py, pipeline/apply/flow.py, llm/spend.py — never learns that a
subscription exists.

`docs/` is absent from this checkout, so paid plans have no PRD id yet; IDEA-21 is the entry
in IDEAS.md. Replace the citation when the PRD section exists.

Two plans, free and plus, with their numbers in .env so they can be tuned without a release.
An account is on plus while it holds a subscription that grants it (database/models.py,
Subscription), and on free otherwise.

Three things are resolved in order, and this order is the whole policy:

1. a number the admin set for this one account (app_user.limit_*) beats everything;
2. else the plan the account's subscription grants;
3. and on top of either, what the owner chose for themselves in Settings, which can only
   lower the figure, never raise it.

The admin is held to no plan. Plus's AI allowance is deliberately far above what its
applications can spend (2 to 3 cents each), because it is a guard against a runaway, not the
thing being sold: a paying account stopped mid-month because the AI ran out would be a
support ticket, not a limit.

A subscription that has stopped paying keeps its plan until the period it paid for ends, and
then some (JAP_BILLING_GRACE_DAYS). Nothing here cuts an account off on the day a renewal
failed: the provider is still retrying, the issuer sends its own notice first, and on Indian
rails a recurring charge is not even attempted until a mandate's pre-debit notice has run.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import AppUser, Subscription
from pipeline.workspace import profile_of

ADMIN_DAILY_APPLICATIONS = 100  # the most profile.daily_cap can hold

FREE = "free"
PLUS = "plus"
# The states a subscription row may be in, and the subset that hands over the plan. pending
# is a checkout nobody has paid for yet, so it grants nothing; past_due grants, see above.
LIVE = ("pending", "active", "past_due")
GRANTING = ("active", "past_due")


@dataclass(frozen=True)
class Plan:
    """A plan as the service defines it. What is actually charged lives at the payment
    provider; price_* here is for display and has to be kept in step with it by hand."""

    code: str
    name: str
    ai_usd: float
    boards: int | None  # companies followed; None: no limit
    applications_per_day: int
    price_inr: int = 0
    price_usd: float = 0.0

    @property
    def paid(self) -> bool:
        return self.code != FREE


def plans() -> dict[str, Plan]:
    s = get_settings()
    return {
        FREE: Plan(
            code=FREE,
            name="Free",
            ai_usd=s.jap_monthly_cost_cap_usd,
            boards=s.jap_free_boards,
            applications_per_day=s.jap_free_daily_applications,
        ),
        PLUS: Plan(
            code=PLUS,
            name="Plus",
            ai_usd=s.jap_plus_ai_usd,
            boards=s.jap_plus_boards,
            applications_per_day=s.jap_plus_daily_applications,
            price_inr=s.jap_plus_price_inr,
            price_usd=s.jap_plus_price_usd,
        ),
    }


def plan_named(code: str) -> Plan:
    """The plan with this code, or free when a row names one that no longer exists: a plan
    withdrawn from sale must not leave its subscribers with no limits at all."""
    return plans().get(code, plans()[FREE])


def grants(sub: Subscription, now: datetime | None = None) -> bool:
    """Whether this subscription hands its plan over right now."""
    if sub.status not in GRANTING:
        return False
    if sub.current_period_end is None:
        return True
    grace = timedelta(days=get_settings().jap_billing_grace_days)
    return (now or datetime.now(UTC)) <= sub.current_period_end + grace


async def live_subscription(session: AsyncSession, user_id: uuid.UUID) -> Subscription | None:
    """The account's one subscription that is not finished, whether or not it is paid up."""
    return await session.scalar(
        select(Subscription).where(Subscription.user_id == user_id, Subscription.status.in_(LIVE))
    )


async def plan_of(session: AsyncSession, user_id: uuid.UUID) -> Plan:
    sub = await live_subscription(session, user_id)
    return plan_named(sub.plan_code) if sub is not None and grants(sub) else plans()[FREE]


@dataclass(frozen=True)
class Limits:
    ai_usd: float  # model spend a month on JAP's key
    boards: int | None  # companies followed; None: no limit
    applications_per_day: int
    # The account's plan, before what its owner chose for themselves inside it.
    plan: str = "Free"
    plan_code: str = FREE
    plan_ai_usd: float = 0.0
    plan_applications: int = 0
    custom: bool = False  # the admin set at least one number for this account


async def limits_for(session: AsyncSession, user_id: uuid.UUID) -> Limits:
    account = await session.get(AppUser, user_id)
    profile = await profile_of(session, user_id)
    admin = account is not None and account.is_admin
    set_ai = float(account.limit_ai_usd) if account and account.limit_ai_usd is not None else None
    set_boards = account.limit_boards if account else None
    set_apps = account.limit_applications if account else None
    plan = await plan_of(session, user_id)

    plan_ai = set_ai if set_ai is not None else plan.ai_usd
    plan_apps = (
        set_apps
        if set_apps is not None
        else ADMIN_DAILY_APPLICATIONS
        if admin
        else plan.applications_per_day
    )
    boards = set_boards if set_boards is not None else None if admin else plan.boards

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
        plan="Admin" if admin else plan.name,
        plan_code="admin" if admin else plan.code,
        plan_ai_usd=plan_ai,
        plan_applications=plan_apps,
        custom=any(v is not None for v in (set_ai, set_boards, set_apps)),
    )
