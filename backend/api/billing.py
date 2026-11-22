"""The plan an account is on, and changing it (IDEA-21).

`docs/` is absent from this checkout, so paid plans have no PRD id yet; IDEA-21 in IDEAS.md
is the entry.

Four routes, all this account's own (PRD S7): what is on offer, start paying, stop paying.
Checkout hands back a page the provider hosts and the browser is sent there, so no card
detail reaches JAP and no payment script runs on its pages.

Cancelling does not end the plan there and then. The provider is told not to renew, and the
plan runs out when the period already paid for does (pipeline/limits.py). Somebody who
cancels on the second day of a month keeps what they bought for the rest of it.
"""

from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ApiError
from auth import limits as auth_limits
from auth.deps import AgreedUser
from billing import service
from billing.providers import PROVIDERS, Provider, ProviderError
from config import get_settings
from database.models import AppUser
from database.session import Session
from pipeline.limits import grants, live_subscription, plan_named, plan_of, plans
from schemas.billing import BillingOut, CheckoutIn, CheckoutOut, PlanOut, SubscriptionOut

router = APIRouter(prefix="/billing", tags=["billing"])

NOT_SELLING = "Plans are not on sale just now"


def sellers() -> list[Provider]:
    """The providers that can take money right now: configured, and selling switched on."""
    if not get_settings().jap_billing_enabled:
        return []
    return [p for p in PROVIDERS.values() if p.configured()]


async def overview(session: AsyncSession, user: AppUser) -> BillingOut:
    sub = await live_subscription(session, user.id)
    on = await plan_of(session, user.id)
    selling = sellers()
    return BillingOut(
        selling=bool(selling),
        providers=[p.name for p in selling],
        plans=[
            PlanOut(
                code=plan.code,
                name=plan.name,
                boards=plan.boards,
                applications_per_day=plan.applications_per_day,
                ai_usd=plan.ai_usd,
                price_inr=plan.price_inr,
                price_usd=plan.price_usd,
                current=plan.code == on.code,
            )
            for plan in plans().values()
        ],
        subscription=None
        if sub is None
        else SubscriptionOut(
            plan_code=sub.plan_code,
            plan_name=plan_named(sub.plan_code).name,
            provider=sub.provider,
            status=sub.status,
            current_period_end=sub.current_period_end,
            cancel_at_period_end=sub.cancel_at_period_end,
            granting=grants(sub),
        ),
    )


@router.get("", response_model=BillingOut)
async def my_plan(user: AgreedUser, session: Session) -> BillingOut:
    return await overview(session, user)


@router.post("/checkout", response_model=CheckoutOut)
async def checkout(
    body: CheckoutIn, request: Request, user: AgreedUser, session: Session
) -> CheckoutOut:
    """Start paying for a plan: the provider creates the subscription, and the browser is
    sent to its page to authorise the payment. Nothing is granted until the provider says
    it was paid (billing/service.py)."""
    await auth_limits.check(session, request, "billing-checkout", user.email)
    provider = next((p for p in sellers() if p.name == body.provider), None)
    if provider is None:
        raise ApiError(409, "not_selling", NOT_SELLING)
    plan = plans().get(body.plan_code)
    if plan is None or not plan.paid:
        raise ApiError(404, "no_such_plan", "There is no plan by that name to buy")
    try:
        out = await service.start_checkout(session, user, plan, provider)
    except service.AlreadySubscribed as exc:
        raise ApiError(409, "already_subscribed", "This account already has a plan") from exc
    except ProviderError as exc:
        raise ApiError(502, "provider_down", "The payment provider did not answer") from exc
    return CheckoutOut(url=out.url)


@router.post("/cancel", response_model=BillingOut)
async def cancel_mine(user: AgreedUser, session: Session) -> BillingOut:
    """Stop the plan renewing. It stays until the period already paid for ends."""
    try:
        await service.cancel(session, user.id)
    except service.NotSubscribed as exc:
        raise ApiError(409, "no_plan", "There is no plan to cancel") from exc
    except ProviderError as exc:
        raise ApiError(502, "provider_down", "The payment provider did not answer") from exc
    return await overview(session, user)
