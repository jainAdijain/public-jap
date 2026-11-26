"""Selling a plan (IDEA-21): the payment providers, and what their webhooks do to a row.

The PRD is absent from this checkout, so paid plans have no PRD id yet. IDEA-21 in IDEAS.md
is the entry; replace the citation when the PRD section exists.

Three files, and the split is deliberate:

- providers.py is the boundary. A provider is four things: start a subscription, cancel one,
  check that a webhook really came from it, and turn that webhook into the one small event
  shape the rest of JAP understands. Nothing outside this package names a provider.
- razorpay.py is the first of them, which sells in India. A merchant of record for everyone
  else is the second and is not written yet: adding it is one file here, one row in
  PROVIDERS, and a CHECK in a migration.
- service.py is everything that is the same whoever is selling: starting a checkout,
  cancelling, and applying an event exactly once and never backwards.

What a plan allows is not here. That is pipeline/limits.py, which reads the subscription row
and knows nothing about who was paid.
"""

from billing.providers import (
    PROVIDERS,
    BadSignature,
    Checkout,
    Provider,
    ProviderError,
    ProviderEvent,
    provider_named,
)
from billing.service import cancel, handle_event, start_checkout

__all__ = [
    "PROVIDERS",
    "BadSignature",
    "Checkout",
    "Provider",
    "ProviderError",
    "ProviderEvent",
    "cancel",
    "handle_event",
    "provider_named",
    "start_checkout",
]
