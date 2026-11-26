"""Creating accounts (PRD S1, S10, S12): who is the admin, what a new account starts with,
the switch that pauses sign-up, and the invite list that lets named people past it.

The invite list (launch decision D4) is how a soft launch admits a fixed group: sign-up stays
paused, and only an address the admin added may create an account, by password or Google.
An address leaves the list the moment its account is made, so the list only ever holds
people who have not signed up yet. It is one app_setting row of lower-cased addresses.

The first account is the admin and needs no email check. A database that had data before it
had any account carries a placeholder owner (migration 0011_tenancy), which the first
sign-up takes over, so that data becomes the admin's (S1). The admin also starts by
following the seeded companies, as the single owner always did; everyone after starts with
an empty workspace and their own empty profile.
"""

import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from auth import sessions
from config import get_settings
from database import tenant
from database.models import (
    AppSetting,
    AppUser,
    BoardFollow,
    EmailVerification,
    Profile,
    WatchedBoard,
)
from pipeline.notify import Mail

UNCLAIMED_EMAIL = "unclaimed@jap.invalid"  # as in migration 0011_tenancy
VERIFY_HOURS = 24
VERIFY_EVERY = timedelta(minutes=2)
SIGN_UP_PAUSED = "sign_up_paused"
SIGN_UP_INVITES = "sign_up_invites"


async def unclaimed(session: AsyncSession) -> AppUser | None:
    found: AppUser | None = await session.scalar(
        select(AppUser).where(AppUser.email == UNCLAIMED_EMAIL, AppUser.password_hash == "")
    )
    return found


async def any_account(session: AsyncSession) -> bool:
    """Whether a real account exists (the placeholder owner is not one)."""
    found = await session.scalar(
        select(AppUser.id).where(AppUser.email != UNCLAIMED_EMAIL).limit(1)
    )
    return found is not None


async def start_workspace(session: AsyncSession, user: AppUser) -> None:
    """A new account's own rows: an empty profile, and for the admin a follow of every
    seeded company. The session becomes the new user's first, which is what lets it write
    rows that are theirs (database/tenant.py). The caller commits."""
    await tenant.use(session, tenant.user(user.id))
    await session.execute(
        insert(Profile).values(user_id=user.id).on_conflict_do_nothing(index_elements=["user_id"])
    )
    if user.is_admin:
        seeded = select(WatchedBoard.id).where(WatchedBoard.origin == "seed")
        for board_id in list(await session.scalars(seeded)):
            await session.execute(
                insert(BoardFollow)
                .values(user_id=user.id, board_id=board_id, active=True)
                .on_conflict_do_nothing(index_elements=["user_id", "board_id"])
            )
            board = await session.get(WatchedBoard, board_id)
            if board is not None:
                board.active = True


async def sign_up_paused(session: AsyncSession) -> bool:
    row = await session.get(AppSetting, SIGN_UP_PAUSED)
    return bool(row.value) if row is not None else False


async def set_sign_up_paused(session: AsyncSession, paused: bool) -> None:
    stmt = insert(AppSetting).values(key=SIGN_UP_PAUSED, value=paused)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["key"], set_={"value": paused, "updated_at": sessions.now()}
        )
    )


async def invites(session: AsyncSession) -> list[str]:
    # A column read, not session.get(): the row may have been changed in this transaction.
    value = await session.scalar(select(AppSetting.value).where(AppSetting.key == SIGN_UP_INVITES))
    return sorted(str(e) for e in value) if isinstance(value, list) else []


async def _save_invites(session: AsyncSession, emails: list[str]) -> None:
    stmt = insert(AppSetting).values(key=SIGN_UP_INVITES, value=emails)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["key"], set_={"value": emails, "updated_at": sessions.now()}
        )
    )


async def invite(session: AsyncSession, email: str) -> None:
    emails = set(await invites(session)) | {email.strip().lower()}
    await _save_invites(session, sorted(emails))


async def uninvite(session: AsyncSession, email: str) -> None:
    emails = set(await invites(session)) - {email.strip().lower()}
    await _save_invites(session, sorted(emails))


async def may_sign_up(session: AsyncSession, email: str) -> bool:
    """Whether a new account may be made for this address now: sign-up is open, or the
    address is on the invite list (D4). The first account never asks (it is the admin)."""
    if not await sign_up_paused(session):
        return True
    return email.strip().lower() in await invites(session)


async def verification_mail(
    session: AsyncSession, user: AppUser, *, force: bool = False
) -> Mail | None:
    """A fresh link for this account's address (S10), or None when one went out less than
    two minutes ago. The caller commits, then sends."""
    last = await session.scalar(
        select(EmailVerification.created_at)
        .where(EmailVerification.user_id == user.id)
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    if not force and last is not None and sessions.now() - last < VERIFY_EVERY:
        return None
    token = secrets.token_urlsafe(32)
    session.add(
        EmailVerification(
            user_id=user.id,
            token_hash=sessions.token_hash(token),
            expires_at=sessions.now() + timedelta(hours=VERIFY_HOURS),
        )
    )
    link = f"{get_settings().jap_ui_url.rstrip('/')}/?verify={token}"
    return Mail(
        to=user.email,
        subject="Confirm your email for JAP",
        body=(
            "Someone (hopefully you) created a JAP account with this address.\n\n"
            f"Confirm it here within {VERIFY_HOURS} hours:\n{link}\n\n"
            "The link works once and signs you in. Until it is opened the account can't be "
            "used.\nIf you didn't create an account, ignore this email.\n"
        ),
    )


async def verify(session: AsyncSession, token: str) -> AppUser | None:
    """Use a link: the account it confirms, or None when it is unknown, used or expired."""
    row = await session.scalar(
        select(EmailVerification)
        .where(EmailVerification.token_hash == sessions.token_hash(token))
        .with_for_update()
    )
    if row is None or row.used_at is not None or row.expires_at <= sessions.now():
        return None
    user = await session.get(AppUser, row.user_id)
    if user is None:
        return None
    row.used_at = sessions.now()
    if user.email_verified_at is None:
        user.email_verified_at = sessions.now()
    return user
