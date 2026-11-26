"""What a payment provider posts back (IDEA-21).

This is the third route that answers without a session, after /api/health and /api/auth/*
(tests/test_auth.py lists them). It has to be: the caller is Razorpay's server, which has no
account here and cannot sign in. What stands in for a session is the signature — every
provider signs the exact bytes it sent, under a secret only it and this service know, and a
body that does not match is Not Found.

Three things this route does and no other route does:

- it reads request.body() before anything else and hands those bytes to verify(). Parsing
  and re-encoding JSON changes the bytes (key order, spacing, unicode escapes) and the
  signature would never match again.
- it answers Not Found, not 401 or 403, to anything unsigned. A prober learns nothing about
  which providers this service uses, and a provider that is misconfigured shows up in the
  log rather than in a stranger's hands.
- it runs as the service (database/tenant.py). Nobody is signed in, and the row it updates
  belongs to a user; with no tenant, row-level security would quite correctly show it no
  rows at all.

It answers 200 as soon as the event is recorded, whatever the event turned out to mean. A
provider retries anything else, and retrying an event that was understood and deliberately
ignored would go on for days.
"""

import logging

from fastapi import APIRouter, Request

from api.errors import ApiError
from billing import service
from billing.providers import BadSignature, provider_named
from database import tenant
from database.session import Session
from schemas.billing import WebhookAck

router = APIRouter(prefix="/webhooks", tags=["billing"])

log = logging.getLogger(__name__)


@router.post("/{provider_name}", response_model=WebhookAck)
async def provider_webhook(provider_name: str, request: Request, session: Session) -> WebhookAck:
    provider = provider_named(provider_name)
    if provider is None or not provider.configured():
        raise ApiError(404, "not_found", "Not Found")
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        provider.verify(raw, headers)
    except BadSignature as exc:
        log.warning("A %s webhook was refused: %s", provider_name, exc)
        raise ApiError(404, "not_found", "Not Found") from exc
    await tenant.use(session, tenant.SYSTEM)
    handled = await service.handle_event(session, provider, raw, headers)
    log.info("%s webhook: %s", provider_name, handled)
    return WebhookAck(ok=True)
