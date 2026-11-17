"""Companies to watch (I8, I9, U8): the boards my runs fetch, grouped by ATS in the UI.

A board is shared and my follow of it is mine (PRD S7): the list, Add and Stop following
all work on board_follow. Adding a company someone else already follows adds only my follow,
and the board keeps being fetched once for all of us. The free plan caps how many companies
one account follows (pipeline/limits.py).

POST /api/boards/detect turns a pasted board URL or a company name into (ATS, token) and
checks the board answers; nothing is saved until the UI then POSTs /api/boards.

DELETE stops following: my jobs from that board close for me, and when nobody follows it
any more the board is switched off and its open postings are closed as unfollowed (I10 to
I15, pipeline/unfollow.py). POST of the same company switches my follow back on; nothing is
fetched until the next search (I16).
"""

import uuid

from fastapi import APIRouter
from sqlalchemy import func, select

from api.errors import ApiError
from auth.deps import CurrentUser
from database.models import BoardFollow, WatchedBoard
from database.session import Session
from pipeline.limits import limits_for
from pipeline.unfollow import effect, follow, follow_of, unfollow
from schemas.board import BoardIn, BoardOut, DetectIn, DetectOut, UnfollowEffect
from schemas.job import ATS
from sources import base
from sources.detect import NotABoard, detect

router = APIRouter(prefix="/boards", tags=["boards"])


def to_out(b: WatchedBoard, active: bool = True) -> BoardOut:
    return BoardOut(
        id=b.id,
        ats=ATS(b.ats),
        board_token=b.board_token,
        display_name=b.display_name,
        origin=b.origin,
        active=active,
        last_checked_at=b.last_checked_at,
        last_ok=b.last_ok,
    )


@router.get("", response_model=list[BoardOut])
async def boards(user: CurrentUser, session: Session) -> list[BoardOut]:
    rows = await session.scalars(
        select(WatchedBoard)
        .join(BoardFollow, BoardFollow.board_id == WatchedBoard.id)
        .where(BoardFollow.user_id == user.id, BoardFollow.active)
        .order_by(WatchedBoard.display_name)
    )
    return [to_out(b) for b in rows]


@router.post("/detect", response_model=DetectOut)
async def detect_board(body: DetectIn, user: CurrentUser, session: Session) -> DetectOut:
    try:
        async with base.client() as http:
            found = await detect(http, body.text)
    except NotABoard as exc:
        raise ApiError(422, "not_a_board", str(exc)) from exc
    watched = await session.scalar(
        select(WatchedBoard.id)
        .join(BoardFollow, BoardFollow.board_id == WatchedBoard.id)
        .where(
            WatchedBoard.ats == found.ats.value,
            WatchedBoard.board_token == found.board_token,
            BoardFollow.user_id == user.id,
            BoardFollow.active,
        )
    )
    return DetectOut(
        ats=found.ats,
        board_token=found.board_token,
        display_name=found.display_name,
        job_count=found.job_count,
        already_watched=watched is not None,
    )


@router.post("", response_model=BoardOut, status_code=201)
async def add(body: BoardIn, user: CurrentUser, session: Session) -> BoardOut:
    token = body.board_token.lower()
    board = await session.scalar(
        select(WatchedBoard)
        .where(WatchedBoard.ats == body.ats, WatchedBoard.board_token == token)
        .with_for_update()
    )
    mine = await follow_of(session, board.id) if board is not None else None
    if mine is not None and mine.active:
        raise ApiError(409, "conflict", f"{body.display_name} is already watched on {body.ats}")
    limit = (await limits_for(session, user.id)).boards
    following = await session.scalar(
        select(func.count())
        .select_from(BoardFollow)
        .where(BoardFollow.user_id == user.id, BoardFollow.active)
    )
    if limit is not None and int(following or 0) >= limit:
        raise ApiError(
            409, "board_limit", f"You can follow {limit} companies. Stop following one first."
        )
    if board is None:
        # The name is the first follower's; a board others already follow keeps its name.
        board = WatchedBoard(
            ats=body.ats, board_token=token, display_name=body.display_name, origin="user"
        )
        session.add(board)
        await session.flush()
    await follow(session, board)
    await session.commit()
    await session.refresh(board)
    return to_out(board)


@router.get("/{board_id}/unfollow", response_model=UnfollowEffect)
async def unfollow_effect(board_id: uuid.UUID, session: Session) -> UnfollowEffect:
    """What stopping following would do, for the confirmation (I15). Changes nothing."""
    board = await session.get(WatchedBoard, board_id)
    if board is None or await follow_of(session, board_id) is None:
        raise ApiError(404, "not_found", "No such board")
    found = await effect(session, board_id)
    return UnfollowEffect(
        display_name=board.display_name,
        hidden=found.hidden,
        kept=found.kept,
        unread=found.unread,
    )


@router.delete("/{board_id}", status_code=204)
async def stop_watching(board_id: uuid.UUID, session: Session) -> None:
    """Stop following: my jobs from the board close for me; the board itself is switched
    off only when nobody follows it any more. Safe to repeat."""
    if await unfollow(session, board_id) is None:
        raise ApiError(404, "not_found", "No such board")
