"""Mail posted to JAP by an inbound-email service (IDEA-16, route B).

Some mail providers drop the address a message was originally sent to when they forward
it, which is exactly the thing that tells two people's security codes apart. An inbound
service does not: it takes delivery of jap+<token>@... itself and posts the message here
with the recipient named, so the match is certain.

This is the one route under /api that no browser session reaches. A mail service cannot
sign in, so it carries a shared secret instead (JAP_INBOUND_SECRET), compared in constant
time; with no secret set the route is off and answers 404 exactly as if it did not exist.
It is registered in main.py outside the signed-in loop for that reason.

What it does is deliberately small: find the account by the token in the recipient, find
that account's one application waiting for a code, read the code out of the text with the
same parser the mailbox path uses, and park it. It never creates or changes anything else,
and it logs no part of the message.
"""

import hmac
import logging

from fastapi import APIRouter, Header
from sqlalchemy import select

from api.errors import ApiError
from config import get_settings
from database import tenant
from database.models import Application, AppUser
from database.session import Session
from observability.logs import event
from pipeline.apply.security_code import code_in
from schemas.inbound import InboundEmail, InboundTaken

router = APIRouter(prefix="/inbound", tags=["inbound"])
log = logging.getLogger("jap.inbound")

OFF = "No such route"


def _token_in(recipient: str) -> str | None:
    """The token out of jap+<token>@domain."""
    name, _, _domain = recipient.strip().lower().partition("@")
    _, plus, token = name.partition("+")
    return token or None if plus else None


@router.post("/email", response_model=InboundTaken)
async def inbound_email(
    body: InboundEmail, session: Session, x_jap_inbound: str = Header(default="")
) -> InboundTaken:
    expected = get_settings().jap_inbound_secret
    if not expected:
        raise ApiError(404, "not_found", OFF)
    if not hmac.compare_digest(x_jap_inbound, expected):
        raise ApiError(401, "unauthorized", "Wrong or missing inbound secret")
    token = _token_in(body.to)
    if token is None:
        raise ApiError(422, "no_token", "The recipient carries no +token")
    user_id = await session.scalar(select(AppUser.id).where(AppUser.code_token == token))
    if user_id is None:
        raise ApiError(404, "not_found", "No account has that address")
    code = code_in(body.text, body.length)
    if code is None:
        return InboundTaken(taken=False)
    # As that account from here on, so row-level security is what keeps this to their own
    # application rather than the care taken writing the query.
    await tenant.use(session, tenant.user(user_id))
    app = await session.scalar(
        select(Application)
        .where(Application.code_wanted_at.is_not(None), Application.status == "applying")
        .order_by(Application.code_wanted_at.desc())
        .limit(1)
        .with_for_update()
    )
    if app is None:
        return InboundTaken(taken=False)
    app.security_code = code
    await session.commit()
    event(log, "inbound.code", "A security code arrived for an application waiting on one")
    return InboundTaken(taken=True)
