"""The admin's controls (PRD S12): who has an account, turning one off, pausing sign-up, and
the three launch switches: AI credit for one account (D1, its limits), whether it may use
Automatic mode (D2), and the invite list that lets named people sign up while sign-up is
paused (D4).

Only the admin reaches these (AdminUser, auth/deps.py); to anyone else they are Not Found.
The list shows what is needed to run the service and nothing a user wrote: an address,
dates, a state and this month's AI spend. Turning an account off signs it out everywhere
and stops its searches and applications; its data is kept, and turning it on restores it.
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ApiError
from api.profile import ai_source, plan_out
from auth import accounts, sessions
from auth.deps import AdminUser
from database.models import AppUser, Profile
from database.session import Session
from llm.spend import month_spend, system_spend, total_spend
from pipeline.limits import limits_for
from schemas.admin import (
    AccountOut,
    Accounts,
    AutomaticSwitch,
    InviteIn,
    LimitsIn,
    SignUpSwitch,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _state(user: AppUser) -> str:
    if user.disabled_at is not None:
        return "off"
    return "active" if user.email_verified_at is not None else "unverified"


async def _overview(session: AsyncSession) -> Accounts:
    users = await session.scalars(
        select(AppUser)
        .where(AppUser.email != accounts.UNCLAIMED_EMAIL)
        .order_by(AppUser.created_at)
    )
    rows = []
    for u in users:
        limits = await limits_for(session, u.id)
        rows.append(
            AccountOut(
                id=u.id,
                email=u.email,
                is_admin=u.is_admin,
                joined_at=u.created_at,
                last_sign_in_at=u.last_sign_in_at,
                state=_state(u),
                spend=await month_spend(session, u.id),
                plan=plan_out(limits),
                ai_source=await ai_source(session, u.id, limits),
                may_automatic=u.is_admin or u.may_automatic,
            )
        )
    return Accounts(
        accounts=rows,
        sign_up_paused=await accounts.sign_up_paused(session),
        invites=await accounts.invites(session),
        spend_total=await total_spend(session),
        spend_shared=await system_spend(session),
    )


@router.get("/accounts", response_model=Accounts)
async def list_accounts(admin: AdminUser, session: Session) -> Accounts:
    return await _overview(session)


async def _account(session: AsyncSession, user_id: uuid.UUID) -> AppUser:
    user = await session.get(AppUser, user_id)
    if user is None or user.email == accounts.UNCLAIMED_EMAIL:
        raise ApiError(404, "not_found", "No such account")
    return user


@router.post("/accounts/{user_id}/off", response_model=Accounts)
async def turn_off(user_id: uuid.UUID, admin: AdminUser, session: Session) -> Accounts:
    """Turn an account off: it is signed out everywhere, can't sign in, and its searches and
    applications stop (S12). The admin's own account can't be turned off."""
    user = await _account(session, user_id)
    if user.is_admin:
        raise ApiError(409, "conflict", "The admin account can't be turned off")
    if user.disabled_at is None:
        user.disabled_at = sessions.now()
    await sessions.end_all(session, user.id)
    await session.commit()
    return await _overview(session)


@router.post("/accounts/{user_id}/on", response_model=Accounts)
async def turn_on(user_id: uuid.UUID, admin: AdminUser, session: Session) -> Accounts:
    user = await _account(session, user_id)
    user.disabled_at = None
    await session.commit()
    return await _overview(session)


@router.put("/accounts/{user_id}/limits", response_model=Accounts)
async def set_limits(
    user_id: uuid.UUID, body: LimitsIn, admin: AdminUser, session: Session
) -> Accounts:
    """Give one account its own limits in place of the plan's (S12): AI spend a month,
    companies followed, applications a day. A number left out goes back to the plan's.
    Lowering the companies limit stops new follows; it unfollows nothing."""
    user = await _account(session, user_id)
    user.limit_ai_usd = Decimal(str(round(body.ai_usd, 2))) if body.ai_usd is not None else None
    user.limit_boards = body.boards
    user.limit_applications = body.applications_per_day
    await session.commit()
    return await _overview(session)


@router.put("/sign-up", response_model=Accounts)
async def sign_up_switch(body: SignUpSwitch, admin: AdminUser, session: Session) -> Accounts:
    """Pause or reopen sign-up (S12). Accounts that exist are not affected."""
    await accounts.set_sign_up_paused(session, body.paused)
    await session.commit()
    return await _overview(session)


@router.put("/accounts/{user_id}/automatic", response_model=Accounts)
async def automatic_switch(
    user_id: uuid.UUID, body: AutomaticSwitch, admin: AdminUser, session: Session
) -> Accounts:
    """Let one account use Automatic mode, or stop it (launch decision D2). Stopping it puts
    the account back on Review mode at once, so the next job it prepares waits for the
    person; a job already prepared keeps the mode it was prepared in."""
    user = await _account(session, user_id)
    if user.is_admin:
        raise ApiError(409, "conflict", "The admin account always may use Automatic mode")
    user.may_automatic = body.allowed
    if not body.allowed:
        profile = await session.scalar(select(Profile).where(Profile.user_id == user.id))
        if profile is not None and profile.apply_mode == "automatic":
            profile.apply_mode = "review"
    await session.commit()
    return await _overview(session)


@router.post("/invites", response_model=Accounts)
async def add_invite(body: InviteIn, admin: AdminUser, session: Session) -> Accounts:
    """Let this address sign up while sign-up is paused (launch decision D4). Nothing is
    emailed: the admin tells the person, who signs up with that address or its Google."""
    if await _taken(session, body.email):
        raise ApiError(409, "conflict", "That address already has an account")
    await accounts.invite(session, body.email)
    await session.commit()
    return await _overview(session)


@router.post("/invites/remove", response_model=Accounts)
async def remove_invite(body: InviteIn, admin: AdminUser, session: Session) -> Accounts:
    await accounts.uninvite(session, body.email)
    await session.commit()
    return await _overview(session)


async def _taken(session: AsyncSession, email: str) -> bool:
    found = await session.scalar(
        select(AppUser.id).where(func.lower(AppUser.email) == email.strip().lower())
    )
    return found is not None
