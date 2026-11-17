"""One user's side of the shared job data (PRD S7): their profile, the companies they
follow, and what each listing is to them.

A listing holds facts about a job and is the same for everyone. Whether it matches, how it
fits my skills and where my application stands live in user_listing, one row per user and
listing, kept only while the listing matches or has a status. A listing with no row is Not
applied while a company I follow still has it open, and Closed otherwise.

Closed is worked out per user: a job is open to me while one of its postings is open on a
board I follow. So a company I stop following closes its jobs for me and leaves them as they
are for everyone else who follows it.
"""

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import ColumnElement, exists, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import tenant
from database.models import AppUser, BoardFollow, Listing, Posting, Profile, UserListing

# Whether listing l has a posting on a board :uid follows; OPEN_SQL also needs it open.
FOLLOWED_SQL = """EXISTS (SELECT 1 FROM posting fp
  JOIN board_follow bf ON bf.board_id = fp.watched_board_id
  WHERE fp.listing_id = l.id AND bf.user_id = :uid AND bf.active)"""
OPEN_SQL = FOLLOWED_SQL.replace("bf.active)", "bf.active AND fp.state = 'open')")

SYNC_SQL = """
UPDATE user_listing ul SET status = o.status
FROM (
  SELECT u.user_id, u.listing_id, CAST(CASE WHEN EXISTS (
      SELECT 1 FROM posting p JOIN board_follow bf ON bf.board_id = p.watched_board_id
      WHERE p.listing_id = u.listing_id AND p.state = 'open'
        AND bf.user_id = u.user_id AND bf.active
    ) THEN 'not_applied' ELSE 'closed' END AS listing_status) AS status
  FROM user_listing u
  WHERE u.status IN ('not_applied', 'closed')
    AND (CAST(:ids AS uuid[]) IS NULL OR u.listing_id = ANY(CAST(:ids AS uuid[])))
    AND (CAST(:uid AS uuid) IS NULL OR u.user_id = CAST(:uid AS uuid))
) o
WHERE ul.user_id = o.user_id AND ul.listing_id = o.listing_id AND ul.status <> o.status
"""


async def profile_of(session: AsyncSession, user_id: uuid.UUID | None = None) -> Profile | None:
    """The user's profile (the session's user unless another is named), or None."""
    uid = user_id or tenant.user_id(session)
    found: Profile | None = await session.scalar(select(Profile).where(Profile.user_id == uid))
    return found


async def load_profile(session: AsyncSession) -> Profile:
    """The session user's profile, created empty the first time."""
    uid = tenant.user_id(session)
    profile = await profile_of(session, uid)
    if profile is None:
        await session.execute(
            insert(Profile).values(user_id=uid).on_conflict_do_nothing(index_elements=["user_id"])
        )
        await session.commit()
        profile = await profile_of(session, uid)
        assert profile is not None
    await session.refresh(profile)
    return profile


async def contact_email(session: AsyncSession, user_id: uuid.UUID | None = None) -> str | None:
    """Where JAP writes to this user (S13): the address on their profile, else the account's."""
    uid = user_id or tenant.user_id(session)
    profile = await profile_of(session, uid)
    if profile is not None and profile.email:
        return str(profile.email)
    account = await session.get(AppUser, uid)
    return account.email if account is not None else None


async def admin_id(session: AsyncSession) -> uuid.UUID | None:
    """The admin account (the first one, S1), which gets what concerns the service."""
    found: uuid.UUID | None = await session.scalar(
        select(AppUser.id).where(AppUser.is_admin).order_by(AppUser.created_at).limit(1)
    )
    return found


async def owner_email(session: AsyncSession) -> str | None:
    """Where JAP writes about this session's work: its user, or the admin for the service's."""
    uid = tenant.of(session).user_id or await admin_id(session)
    return await contact_email(session, uid) if uid is not None else None


def follows(user_id: uuid.UUID) -> Any:
    """The ids of the boards this user follows now, for an IN (...)."""
    return select(BoardFollow.board_id).where(BoardFollow.user_id == user_id, BoardFollow.active)


def followed(user_id: uuid.UUID, *, open_only: bool = False) -> ColumnElement[bool]:
    """This Listing has a posting (an open one) on a board the user follows."""
    where = [Posting.listing_id == Listing.id, Posting.watched_board_id.in_(follows(user_id))]
    if open_only:
        where.append(Posting.state == "open")
    return exists().where(*where)


async def is_open(session: AsyncSession, listing_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    found = await session.scalar(
        select(Posting.id)
        .where(
            Posting.listing_id == listing_id,
            Posting.state == "open",
            Posting.watched_board_id.in_(follows(user_id)),
        )
        .limit(1)
    )
    return found is not None


async def state_of(
    session: AsyncSession, listing_id: uuid.UUID, *, lock: bool = False
) -> UserListing | None:
    """The session user's row for this listing, or None when it has none yet."""
    # populate_existing: set_status writes the row with an upsert, behind the session's back.
    query = (
        select(UserListing)
        .where(UserListing.user_id == tenant.user_id(session), UserListing.listing_id == listing_id)
        .execution_options(populate_existing=True)
    )
    found: UserListing | None = await session.scalar(query.with_for_update() if lock else query)
    return found


async def status_of(session: AsyncSession, listing_id: uuid.UUID) -> str:
    """The listing's status for the session user, row or no row."""
    row = await state_of(session, listing_id)
    if row is not None:
        return str(row.status)
    open_to_me = await is_open(session, listing_id, tenant.user_id(session))
    return "not_applied" if open_to_me else "closed"


async def can_see(session: AsyncSession, listing_id: uuid.UUID) -> bool:
    """Whether the listing is the session user's to open: it is on a board they follow, or
    they have a status on it (an application made before they stopped following)."""
    uid = tenant.user_id(session)
    found = await session.scalar(
        select(Listing.id).where(
            Listing.id == listing_id,
            followed(uid)
            | exists().where(UserListing.listing_id == Listing.id, UserListing.user_id == uid),
        )
    )
    return found is not None


async def set_status(
    session: AsyncSession, listing_id: uuid.UUID, status: str, **more: Any
) -> None:
    """Write the session user's status for a listing, creating their row if need be."""
    stmt = insert(UserListing).values(
        user_id=tenant.user_id(session), listing_id=listing_id, status=status, **more
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id", "listing_id"], set_={"status": status, **more}
        )
    )


async def sync_status(
    session: AsyncSession,
    listing_ids: Iterable[uuid.UUID] | None = None,
    user_id: uuid.UUID | None = None,
) -> None:
    """Move Not applied and Closed to what the postings say now, for these listings (all)
    and this user (everyone the session may see). Any other status is left alone: a job
    with an application is never closed or reset (I12)."""
    await session.execute(
        text(SYNC_SQL),
        {"ids": None if listing_ids is None else list(listing_ids), "uid": user_id},
    )
