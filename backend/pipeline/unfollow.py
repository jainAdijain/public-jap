"""Following and stopping following a company (PRD I8, I10 to I15, S7): nothing is deleted.

A board is shared; who follows it is not. Each user has their own follow (board_follow), and
watched_board.active says whether anyone does, which is what decides whether the board is
fetched and its postings read.

When I stop following, my jobs from that board close for me: every Not applied job left
with no open posting on a board I still follow becomes Closed in my list
(workspace.sync_status). A job merged from two boards stays open while I follow the other.
A job with an application is never closed or reset, so Sent, Failed and in-progress ones
keep their status, and hidden ones stay hidden. Nobody else's list changes.

When the last follower leaves, the board is switched off and its open postings are marked
Closed with the reason unfollowed, as before: nothing fetches them any more, so they would
otherwise go stale as open. Following again only switches my follow, and the board, back on
(api/boards.py). The next search reopens what the board still lists, on the same rows
(pipeline/ingest.py).

`python -m pipeline.unfollow` closes what boards nobody follows left open. It only reports
unless given --apply.
"""

import asyncio
import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import tenant
from database.models import BoardFollow, Listing, Posting, UserListing, WatchedBoard
from database.session import get_sessionmaker
from pipeline.dedup import refresh_listings
from pipeline.workspace import follows, sync_status

IN_HISTORY = ("ready_for_review", "applying", "applied", "needs_input", "failed", "manual_apply")


@dataclass
class Effect:
    """What unfollowing a board does, in jobs (listings) as the Jobs page counts them."""

    open_postings: int = 0
    hidden: int = 0  # Not applied jobs that leave the Jobs page
    kept: int = 0  # jobs with an application: they stay in my history
    unread: int = 0  # postings whose AI reading is no longer done


async def follow_of(
    session: AsyncSession, board_id: uuid.UUID, *, lock: bool = False
) -> BoardFollow | None:
    """The session user's follow of this board, active or not."""
    query = select(BoardFollow).where(
        BoardFollow.user_id == tenant.user_id(session), BoardFollow.board_id == board_id
    )
    found: BoardFollow | None = await session.scalar(query.with_for_update() if lock else query)
    return found


async def followers(session: AsyncSession, board_id: uuid.UUID) -> int:
    """How many users follow the board now. Counted as the service: a user sees only
    their own follow."""
    async with tenant.elevated(session):
        n = await session.scalar(
            select(func.count())
            .select_from(BoardFollow)
            .where(BoardFollow.board_id == board_id, BoardFollow.active)
        )
    return int(n or 0)


async def follow(session: AsyncSession, board: WatchedBoard) -> None:
    """Follow the board as the session user and switch it on. The caller commits."""
    stmt = insert(BoardFollow).values(
        user_id=tenant.user_id(session), board_id=board.id, active=True
    )
    await session.execute(
        stmt.on_conflict_do_update(index_elements=["user_id", "board_id"], set_={"active": True})
    )
    board.active = True
    await session.flush()
    # Jobs of this board I had closed by unfollowing are open to me again.
    on_board = select(Posting.listing_id).where(Posting.watched_board_id == board.id)
    ids = [i for i in await session.scalars(on_board) if i is not None]
    await sync_status(session, ids, tenant.user_id(session))


def _other_open(user_id: uuid.UUID, board_id: uuid.UUID):  # type: ignore[no-untyped-def]
    """This listing has an open posting on another board I follow."""
    other = Posting.__table__.alias("other")
    return (
        select(other.c.id)
        .where(
            other.c.listing_id == Listing.id,
            other.c.watched_board_id != board_id,
            other.c.state == "open",
            other.c.watched_board_id.in_(follows(user_id)),
        )
        .exists()
    )


async def effect(session: AsyncSession, board_id: uuid.UUID) -> Effect:
    """What unfollowing would do to my list, without doing it (the confirmation, I15)."""
    uid = tenant.user_id(session)
    mine = [Posting.watched_board_id == board_id, Posting.state == "open"]
    out = Effect()
    out.open_postings = int(
        await session.scalar(select(func.count()).select_from(Posting).where(*mine)) or 0
    )
    if await followers(session, board_id) <= 1:  # the AI stops reading it only when I am the last
        out.unread = int(
            await session.scalar(
                select(func.count())
                .select_from(Posting)
                .where(*mine, Posting.extracted_hash.is_distinct_from(Posting.description_hash))
            )
            or 0
        )
    on_board = select(Posting.listing_id).where(*mine, Posting.listing_id.is_not(None))
    listings = (
        select(func.count())
        .select_from(Listing)
        .join(UserListing, UserListing.listing_id == Listing.id)
        .where(UserListing.user_id == uid, Listing.id.in_(on_board))
    )
    out.hidden = int(
        await session.scalar(
            listings.where(
                UserListing.status == "not_applied",
                UserListing.matched.is_not(None),
                ~_other_open(uid, board_id),
            )
        )
        or 0
    )
    out.kept = int(await session.scalar(listings.where(UserListing.status.in_(IN_HISTORY))) or 0)
    return out


async def close_board(session: AsyncSession, board_id: uuid.UUID) -> int:
    """Close the board's open postings as unfollowed and refresh their listings. The caller
    holds the board row locked and commits. Returns how many postings were closed."""
    rows = (
        await session.execute(
            update(Posting)
            .where(Posting.watched_board_id == board_id, Posting.state == "open")
            .values(state="closed", closed_at=func.now(), close_reason="unfollowed")
            .returning(Posting.listing_id)
        )
    ).all()
    await refresh_listings(session, {lid for (lid,) in rows if lid is not None})
    return len(rows)


async def unfollow(session: AsyncSession, board_id: uuid.UUID) -> Effect | None:
    """Stop following the board, in one transaction. None: I don't follow such a board.

    The board row is locked first, so a search storing this board's jobs right now either
    finishes before (and what it stored is closed here) or finds the board off (I14)."""
    uid = tenant.user_id(session)
    board = await session.get(WatchedBoard, board_id, with_for_update=True)
    mine = await follow_of(session, board_id, lock=True) if board is not None else None
    if board is None or mine is None:
        return None
    out = await effect(session, board_id)
    mine.active = False
    await session.flush()
    on_board = select(Posting.listing_id).where(Posting.watched_board_id == board_id)
    ids = [i for i in await session.scalars(on_board) if i is not None]
    if await followers(session, board_id) == 0:
        async with tenant.elevated(session):  # the shared half: nobody follows it now
            board.active = False
            await close_board(session, board_id)
    await sync_status(session, ids, uid)
    await session.commit()
    return out


async def leftovers(session: AsyncSession) -> list[tuple[WatchedBoard, int]]:
    """Boards nobody follows that still have open postings, with how many."""
    rows = await session.execute(
        select(WatchedBoard, func.count(Posting.id))
        .join(Posting, Posting.watched_board_id == WatchedBoard.id)
        .where(WatchedBoard.active.is_(False), Posting.state == "open")
        .group_by(WatchedBoard.id)
        .order_by(WatchedBoard.display_name)
    )
    return [(board, int(n)) for board, n in rows.all()]


async def close_leftovers(apply: bool) -> str:
    lines: list[str] = []
    with tenant.as_system():
        async with get_sessionmaker()() as session:
            found = await leftovers(session)
            total = 0
            for board, n in found:
                total += n
                lines.append(f"  {board.display_name} ({board.ats}): {n} open postings")
                if apply:
                    await session.get(WatchedBoard, board.id, with_for_update=True)
                    await close_board(session, board.id)
                    await session.commit()
    if not found:
        return "No unfollowed company has open postings. Nothing to do."
    verb = "Closed" if apply else "Would close"
    lines.append(f"{verb} {total} postings of {len(found)} unfollowed companies.")
    if not apply:
        lines.append("Nothing was changed. Run again with APPLY=1 to close them.")
    return "\n".join(lines)


if __name__ == "__main__":
    print(asyncio.run(close_leftovers("--apply" in sys.argv[1:])))
