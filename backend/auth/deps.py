"""The dependencies every non-public route carries (PRD S6, S7, S12, S14).

current_user: a live signed-in session of an account that is confirmed and not turned off.
It also makes the request's database session that user's (database/tenant.py), so from here
on the session reads and writes only their rows, in the code and again in Postgres.

agreed_user: the same, and the account has agreed to the notice as it stands (S14). Every
route that works on a user's own data carries this one, not current_user, so an old browser
tab or anything calling the API directly is refused until the tick is given. /api/auth is
left out of it, because /api/auth/terms is how the tick is given.

admin_user: the same, for the admin only. Anyone else gets Not Found, as for a route that
does not exist. An admin route works on the service as a whole (every user's runs, the
shared listings), so its session is the service's; it names a user where it needs one.
"""

from typing import Annotated

from fastapi import Depends, Request

from api.errors import ApiError
from auth import consent, sessions
from database import tenant
from database.models import AppUser, AuthSession
from database.session import Session
from observability import context


async def current_session(request: Request, session: Session) -> AuthSession:
    row = await sessions.find(session, request, sessions.SIGNED_IN)
    if row is None:
        raise ApiError(401, "signed_out", "Sign in to continue")
    await sessions.touch(session, row)
    return row


async def current_user(
    row: Annotated[AuthSession, Depends(current_session)], session: Session
) -> AppUser:
    user = await session.get(AppUser, row.user_id)
    if user is None or user.disabled_at is not None or user.email_verified_at is None:
        raise ApiError(401, "signed_out", "Sign in to continue")
    await tenant.use(session, tenant.user(user.id))
    context.bind(user_id=user.id)
    return user


async def agreed_user(user: Annotated[AppUser, Depends(current_user)]) -> AppUser:
    """Signed in, and agreed to the terms and the privacy notice as they stand (S14). A
    notice that has changed since takes every account back through the tick, so what JAP
    does for someone is always something they have been told about and agreed to."""
    if not consent.accepted_terms(user):
        raise ApiError(403, "terms_not_accepted", consent.NEEDS_TERMS)
    return user


async def admin_user(user: Annotated[AppUser, Depends(agreed_user)], session: Session) -> AppUser:
    if not user.is_admin:
        raise ApiError(404, "not_found", "Not Found")
    await tenant.use(session, tenant.SYSTEM)
    return user


CurrentSession = Annotated[AuthSession, Depends(current_session)]
CurrentUser = Annotated[AppUser, Depends(current_user)]
AgreedUser = Annotated[AppUser, Depends(agreed_user)]
AdminUser = Annotated[AppUser, Depends(admin_user)]
