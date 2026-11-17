"""The admin's controls (PRD S12): who has an account, turning one off, and pausing sign-up.

Only the admin reaches these (AdminUser, auth/deps.py); to anyone else they are Not Found.
The list shows what is needed to run the service and nothing a user wrote: an address,
dates, a state and this month's AI spend. Turning an account off signs it out everywhere
and stops its searches and applications; its data is kept, and turning it on restores it.
"""

import uuid

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ApiError
from auth import accounts, sessions
from auth.deps import AdminUser
from database.models import AppUser
from database.session import Session
from llm.spend import month_spend, system_spend, total_spend
from schemas.admin import AccountOut, Accounts, SignUpSwitch

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
    return Accounts(
        accounts=[
            AccountOut(
                id=u.id,
                email=u.email,
                is_admin=u.is_admin,
                joined_at=u.created_at,
                last_sign_in_at=u.last_sign_in_at,
                state=_state(u),
                spend=await month_spend(session, u.id),
            )
            for u in users
        ],
        sign_up_paused=await accounts.sign_up_paused(session),
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


@router.put("/sign-up", response_model=Accounts)
async def sign_up_switch(body: SignUpSwitch, admin: AdminUser, session: Session) -> Accounts:
    """Pause or reopen sign-up (S12). Accounts that exist are not affected."""
    await accounts.set_sign_up_paused(session, body.paused)
    await session.commit()
    return await _overview(session)
