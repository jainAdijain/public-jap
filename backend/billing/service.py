"""Starting, stopping and keeping up with a subscription (IDEA-21).

Everything here is the same whoever is selling; what differs is behind billing/providers.py.

handle_event() is the careful one, because a webhook is the one part of JAP that a stranger
can call. Three rules, and each exists because providers really do this:

- it is applied exactly once. billing_event has UNIQUE (provider, event_id) and the handler
  inserts before it acts, in the same transaction, so a retry of a delivery that already
  landed finds the row and stops. If anything later in the transaction fails, the event row
  goes too and the provider's next retry is a fresh attempt rather than a silent no-op.
- it is never applied backwards. Deliveries overtake each other; an event older than the
  state the row already holds (subscription.state_at) is recorded and dropped, or a late
  "a charge failed" would put a paid-up account back into arrears.
- it is applied by the service, not by a user. The route puts the session in the service's
  tenant (database/tenant.py) because nobody is signed in and the row belongs to someone.

A cancellation never takes the plan away there and then: the provider is told to stop
renewing at the end of the period already paid for, the row is marked, and the plan runs out
by itself in pipeline/limits.py.
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from billing.providers import Checkout, Provider, ProviderError, ProviderEvent, provider_named
from database.models import AppUser, BillingEvent, Subscription
from pipeline.limits import Plan, grants, live_subscription

log = logging.getLogger(__name__)


class AlreadySubscribed(Exception):
    """This account already has a plan it is paying for."""


class NotSubscribed(Exception):
    """There is nothing to cancel."""


def _older(when: datetime | None, than: datetime | None) -> bool:
    """Whether this delivery has been overtaken by one already applied. A provider makes no
    promise about the order they arrive in, only that each is sent at least once."""
    return when is not None and than is not None and when < than


async def _retire(session: AsyncSession, sub: Subscription) -> None:
    """Put an unfinished checkout out of the way so another can start. Telling the provider
    is best effort: a subscription nobody ever paid for costs nothing if it lingers there,
    and a provider that is down must not stop someone trying again."""
    provider = provider_named(sub.provider)
    if provider is not None:
        try:
            await provider.cancel(sub.provider_ref)
        except ProviderError as exc:
            log.warning("Abandoned checkout not cancelled at %s: %s", sub.provider, exc)
    sub.status = "expired"
    await session.flush()


async def start_checkout(
    session: AsyncSession, user: AppUser, plan: Plan, provider: Provider
) -> Checkout:
    """Create the subscription at the provider and the pending row here, and give back the
    page to send the browser to. Nothing is paid yet: the row grants no plan until the
    provider says it was (pipeline/limits.py)."""
    live = await live_subscription(session, user.id)
    if live is not None and grants(live):
        raise AlreadySubscribed(live.plan_code)
    if live is not None:
        await _retire(session, live)
    out = await provider.start(plan, user)
    session.add(
        Subscription(
            user_id=user.id,
            provider=provider.name,
            provider_ref=out.provider_ref,
            plan_code=plan.code,
            status="pending",
        )
    )
    await session.commit()
    return out


async def cancel(session: AsyncSession, user_id: uuid.UUID) -> Subscription:
    """Stop it renewing. The plan stays until the period already paid for runs out."""
    sub = await live_subscription(session, user_id)
    if sub is None or not grants(sub):
        raise NotSubscribed()
    provider = provider_named(sub.provider)
    if provider is None:
        raise ProviderError(f"No such provider: {sub.provider}")
    await provider.cancel(sub.provider_ref)
    sub.cancel_at_period_end = True
    await session.commit()
    return sub


async def cancel_quietly(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Stop charging an account that is being deleted (PRD S15). Best effort and never
    raises: a deletion must go through whether or not the provider answers, but it must not
    leave a mandate behind that keeps taking money from someone who has no account."""
    try:
        sub = await live_subscription(session, user_id)
        if sub is None:
            return
        provider = provider_named(sub.provider)
        if provider is not None:
            await provider.cancel(sub.provider_ref)
    except Exception as exc:  # noqa: BLE001 - the deletion matters more than the reason
        log.error("Subscription not cancelled before an account was deleted: %s", exc)


async def handle_event(
    session: AsyncSession, provider: Provider, raw: bytes, headers: dict[str, str]
) -> str:
    """Apply one verified webhook. Returns what it did, for the billing_event row and the
    log line. The caller has already checked the signature."""
    event = provider.read(raw, headers)
    if event is None:
        return "unreadable"
    recorded = await session.scalar(
        insert(BillingEvent)
        .values(
            provider=provider.name,
            event_id=event.event_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            provider_ref=event.provider_ref,
            status=event.status,
            amount=event.amount,
            currency=event.currency,
        )
        .on_conflict_do_nothing(index_elements=["provider", "event_id"])
        .returning(BillingEvent.id)
    )
    if recorded is None:
        return "repeat"  # this delivery has been handled before; do nothing at all
    handled = await _apply(session, provider, event)
    await session.execute(
        update(BillingEvent).where(BillingEvent.id == recorded).values(handled=handled)
    )
    await session.commit()
    return handled


async def _apply(session: AsyncSession, provider: Provider, event: ProviderEvent) -> str:
    if event.provider_ref is None:
        return "no subscription named"
    sub = await session.scalar(
        select(Subscription).where(
            Subscription.provider == provider.name,
            Subscription.provider_ref == event.provider_ref,
        )
    )
    if sub is None:
        # A subscription this database never created, or one left over from another stack
        # pointed at the same provider account. Recorded, and otherwise ignored.
        return "no such subscription here"
    if event.status is None:
        return f"{event.event_type}: nothing to apply"
    if _older(event.occurred_at, sub.state_at):
        return "older than the state held"
    sub.status = event.status
    sub.state_at = event.occurred_at
    if event.current_period_end is not None:
        sub.current_period_end = event.current_period_end
    return f"{event.event_type}: {event.status}"
