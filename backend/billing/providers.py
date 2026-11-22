"""The boundary every payment provider is behind (IDEA-21).

A provider does four things and JAP knows nothing else about it:

- start(): create a subscription and give back somewhere to send the browser. A hosted page
  the provider owns, always — a redirect, not a script on JAP's own pages. That keeps card
  details off this service entirely, keeps the Content-Security-Policy as tight as it is
  (infra/caddy/Caddyfile), and means one checkout flow whoever is selling.
- cancel(): stop it renewing at the end of the period already paid for, never sooner.
- verify(): prove the webhook came from the provider, against the raw body. Every provider
  signs the bytes it sent, so the body must not be parsed, re-encoded or touched first.
- read(): turn that webhook into a ProviderEvent, which is the only shape service.py sees.

status on a ProviderEvent is already in JAP's own words (pipeline/limits.py LIVE), not the
provider's, because mapping a provider's vocabulary is the provider's job. None means the
event says nothing about the state — it is recorded and nothing is applied.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from database.models import AppUser
from pipeline.limits import Plan


class ProviderError(RuntimeError):
    """The provider refused or could not be reached."""


class BadSignature(Exception):
    """A webhook that is not signed by the provider it claims to be from."""


@dataclass(frozen=True)
class Checkout:
    """A subscription waiting to be paid for, and where the payer finishes it."""

    provider_ref: str  # the provider's own id for the subscription
    url: str  # the hosted page to send the browser to


@dataclass(frozen=True)
class ProviderEvent:
    """One webhook, in JAP's words. Only event_id and event_type are always there."""

    event_id: str
    event_type: str
    occurred_at: datetime | None = None
    provider_ref: str | None = None
    status: str | None = None  # pending, active, past_due, cancelled, expired
    current_period_end: datetime | None = None
    amount: Decimal | None = None
    currency: str | None = None


class Provider(Protocol):
    name: str

    def configured(self) -> bool:
        """Whether this provider has the keys it needs to be used at all."""

    async def start(self, plan: Plan, user: AppUser) -> Checkout: ...

    async def cancel(self, provider_ref: str) -> None: ...

    def verify(self, raw: bytes, headers: Mapping[str, str]) -> None:
        """Raise BadSignature unless these exact bytes came from the provider."""

    def read(self, raw: bytes, headers: Mapping[str, str]) -> ProviderEvent | None: ...


def _registry() -> dict[str, Provider]:
    from billing.razorpay import Razorpay

    return {Razorpay.name: Razorpay()}


PROVIDERS: dict[str, Provider] = _registry()


def provider_named(name: str) -> Provider | None:
    return PROVIDERS.get(name)
