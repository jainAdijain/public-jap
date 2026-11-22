"""What the Plan screen reads and sends (IDEA-21)."""

from datetime import datetime

from pydantic import BaseModel, Field


class PlanOut(BaseModel):
    """One plan as it is offered. The prices are what Settings shows; what is charged is
    the price held by the payment provider (pipeline/limits.py)."""

    code: str
    name: str
    boards: int | None  # companies followed; None: no limit
    applications_per_day: int
    ai_usd: float
    price_inr: int
    price_usd: float
    current: bool  # the plan this account is on right now


class SubscriptionOut(BaseModel):
    plan_code: str
    plan_name: str
    provider: str
    # pending (not paid for yet), active, past_due (a renewal failed and is being retried),
    # cancelled or expired.
    status: str
    current_period_end: datetime | None
    cancel_at_period_end: bool
    # Whether it is handing its plan over at this moment: a pending one never does, and a
    # past_due one does until the period it paid for has run out and the grace after it.
    granting: bool


class BillingOut(BaseModel):
    selling: bool  # a provider is configured and selling is switched on
    providers: list[str]
    plans: list[PlanOut]
    subscription: SubscriptionOut | None


class CheckoutIn(BaseModel):
    plan_code: str = Field(max_length=40)
    provider: str = Field(max_length=40)


class CheckoutOut(BaseModel):
    """Where to send the browser. A page the provider hosts, never a script on JAP's."""

    url: str


class WebhookAck(BaseModel):
    ok: bool
