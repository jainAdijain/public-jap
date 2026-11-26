"""Razorpay, which sells Plus in India (IDEA-21).

The flow is deliberately the dullest one Razorpay offers. A subscription is created over the
API and the browser is sent to the hosted page it comes back with (`short_url`), where the
payer authorises a mandate on a card or UPI. JAP never loads Razorpay's checkout script, so
no card detail and no third-party script ever touches these pages.

Nothing personal is sent. A subscription is created with no customer attached: the payer
types their own name, email and card into Razorpay's page. The only thing JAP puts on the
row is the account's id in `notes`, which is an opaque id and not a fact about anybody.

Webhooks are signed with HMAC-SHA256 over the exact bytes posted, under the secret set on
the webhook in Razorpay's dashboard. Razorpay's own documentation is explicit that the body
must not be parsed or re-encoded before the signature is checked, which is why verify()
takes bytes and the route reads request.body() before anything else.

Recurring charges on Indian rails are not immediate: the RBI's e-mandate framework has the
issuer notify the payer at least 24 hours before each debit, and the mandate itself is
authenticated when it is registered and on the first charge. So a renewal is in flight for
a day or more before it succeeds or fails, and nothing here treats a late charge as a
lapsed plan — pipeline/limits.py keeps the plan to the end of the paid period and past it.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from billing.providers import BadSignature, Checkout, ProviderError, ProviderEvent
from config import get_settings
from database.models import AppUser
from pipeline.limits import PLUS, Plan

# A monthly subscription has to be sold a fixed number of cycles ahead; ten years of them is
# Razorpay's way of saying "until it is cancelled".
TOTAL_COUNT = 120
TIMEOUT = 20.0

# Razorpay's states in JAP's words (pipeline/limits.py). An event that is not here says
# nothing about the state: it is recorded and nothing is applied.
STATUS = {
    "subscription.authenticated": "pending",  # the mandate is signed, nothing is paid yet
    "subscription.activated": "active",
    "subscription.charged": "active",
    "subscription.resumed": "active",
    "subscription.pending": "past_due",  # a charge failed; Razorpay is still retrying
    "subscription.halted": "past_due",  # the retries are done, the payer has to act
    "subscription.paused": "past_due",
    "subscription.cancelled": "cancelled",
    "subscription.completed": "expired",
}


def _at(value: Any) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if isinstance(value, int | float) else None


def _entity(payload: dict[str, Any], name: str) -> dict[str, Any]:
    part = payload.get("payload", {})
    found = part.get(name, {}).get("entity") if isinstance(part, dict) else None
    return found if isinstance(found, dict) else {}


class Razorpay:
    name = "razorpay"

    def configured(self) -> bool:
        s = get_settings()
        return bool(s.razorpay_key_id and s.razorpay_key_secret and s.razorpay_plan_id_plus)

    def plan_ref(self, plan: Plan) -> str:
        if plan.code != PLUS:
            raise ProviderError(f"No Razorpay plan is configured for {plan.code}")
        return get_settings().razorpay_plan_id_plus

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        s = get_settings()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(
                    f"{s.razorpay_url.rstrip('/')}{path}",
                    json=body,
                    auth=(s.razorpay_key_id, s.razorpay_key_secret),
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Razorpay not reachable: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            # The body can quote what the payer typed, so only the status is passed on.
            raise ProviderError(f"Razorpay answered {resp.status_code}")
        out: dict[str, Any] = resp.json()
        return out

    async def start(self, plan: Plan, user: AppUser) -> Checkout:
        body = await self._post(
            "/v1/subscriptions",
            {
                "plan_id": self.plan_ref(plan),
                "total_count": TOTAL_COUNT,
                "customer_notify": 1,  # Razorpay sends the receipts; JAP does not duplicate them
                "notes": {"user_id": str(user.id)},
            },
        )
        ref, url = body.get("id"), body.get("short_url")
        if not isinstance(ref, str) or not isinstance(url, str):
            raise ProviderError("Razorpay created a subscription with no id or no page")
        return Checkout(provider_ref=ref, url=url)

    async def cancel(self, provider_ref: str) -> None:
        # At the end of the cycle already paid for, never in the middle of one.
        await self._post(f"/v1/subscriptions/{provider_ref}/cancel", {"cancel_at_cycle_end": 1})

    def verify(self, raw: bytes, headers: Mapping[str, str]) -> None:
        secret = get_settings().razorpay_webhook_secret
        if not secret:
            raise BadSignature("No Razorpay webhook secret is set")
        sent = headers.get("x-razorpay-signature", "")
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not sent or not hmac.compare_digest(sent, expected):
            raise BadSignature("The signature does not match the body")

    def read(self, raw: bytes, headers: Mapping[str, str]) -> ProviderEvent | None:
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        event_type = payload.get("event")
        if not isinstance(event_type, str):
            return None
        sub = _entity(payload, "subscription")
        payment = _entity(payload, "payment")
        amount = payment.get("amount")
        return ProviderEvent(
            # Razorpay's own id for the delivery; without it the body itself identifies it,
            # so a retry of the same event is still recognised as the same event.
            event_id=headers.get("x-razorpay-event-id") or hashlib.sha256(raw).hexdigest(),
            event_type=event_type,
            occurred_at=_at(payload.get("created_at")),
            provider_ref=sub.get("id") if isinstance(sub.get("id"), str) else None,
            status=STATUS.get(event_type),
            current_period_end=_at(sub.get("current_end")),
            # Razorpay counts in the smallest unit of the currency.
            amount=Decimal(amount) / 100 if isinstance(amount, int) else None,
            currency=payment.get("currency") if isinstance(payment.get("currency"), str) else None,
        )
