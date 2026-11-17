"""Postings: one job on one board. The dashboard lists listings (api/listings.py); this is
the raw per-board view, the JD detail of one posting, and Split (D5).
Jobs of a company I stopped watching are left out (they are kept, just no longer shown).
A posting is shared, and I see the ones on boards I follow (PRD S7); any other answers Not
Found. Split changes the shared listing for everyone, so it is the admin's.
"""

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import ColumnElement, func, or_, select

from api.errors import ApiError
from auth.deps import AdminUser, CurrentUser
from database.models import Posting, UserListing
from database.session import Session
from pipeline.dedup import DedupConflict, split_posting
from pipeline.matching import refilter_users
from pipeline.workspace import follows
from schemas.extract import Extracted
from schemas.job import ATS
from schemas.listing import SplitOut
from schemas.posting import PostingDetail, PostingPage, PostingRow

router = APIRouter(prefix="/postings", tags=["postings"])


def _row(p: Posting) -> PostingRow:
    return PostingRow(
        id=p.id,
        source=ATS(p.source),
        company=p.company_name_raw,
        title=p.title_raw,
        locations=p.locations,
        posted_at=p.posted_at,
        state=p.state,
        extracted=p.extracted_hash == p.description_hash,
        work_mode=(p.extracted or {}).get("work_mode"),
        first_seen_at=p.first_seen_at,
    )


@router.get("", response_model=PostingPage)
async def postings(
    user: CurrentUser,
    session: Session,
    state: str = Query("open", pattern="^(open|closed|all)$"),
    q: str | None = Query(None, max_length=100),
    source: ATS | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PostingPage:
    where: list[ColumnElement[bool]] = [Posting.watched_board_id.in_(follows(user.id))]
    if state != "all":
        where.append(Posting.state == state)
    if source is not None:
        where.append(Posting.source == source.value)
    if q:
        like = f"%{q}%"
        where.append(or_(Posting.title_raw.ilike(like), Posting.company_name_raw.ilike(like)))
    total = await session.scalar(select(func.count()).select_from(Posting).where(*where))
    rows = await session.scalars(
        select(Posting)
        .where(*where)
        .order_by(Posting.posted_at.desc().nulls_last(), Posting.title_raw)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return PostingPage(
        total=int(total or 0), page=page, page_size=page_size, rows=[_row(p) for p in rows]
    )


def detail(p: Posting) -> PostingDetail:
    return PostingDetail(
        **_row(p).model_dump(),
        department=p.department,
        employment_type=p.employment_type,
        requisition_id=p.requisition_id,
        posting_url=p.posting_url,
        apply_url=p.apply_url,
        description_text=p.description_text,
        fields=Extracted.model_validate(p.extracted) if p.extracted else None,
        closed_at=p.closed_at,
    )


@router.get("/{posting_id}", response_model=PostingDetail)
async def posting(posting_id: uuid.UUID, user: CurrentUser, session: Session) -> PostingDetail:
    mine = select(UserListing.listing_id).where(UserListing.user_id == user.id)
    p = await session.scalar(
        select(Posting).where(
            Posting.id == posting_id,
            or_(Posting.watched_board_id.in_(follows(user.id)), Posting.listing_id.in_(mine)),
        )
    )
    if p is None:
        raise ApiError(404, "not_found", "No such job")
    return detail(p)


@router.post("/{posting_id}/split", response_model=SplitOut)
async def split(posting_id: uuid.UUID, admin: AdminUser, session: Session) -> SplitOut:
    """Split this posting out of its listing (D5); remembered on later runs. The listing is
    shared, so this is the admin's call and it changes everyone's list (PRD S7)."""
    try:
        p = await session.get(Posting, posting_id)
        old = p.listing_id if p is not None else None
        listing_id = await split_posting(session, posting_id)
        # Both halves get their matched conditions, for everyone who has either.
        await refilter_users(session, [i for i in (old, listing_id) if i is not None])
        await session.commit()
    except LookupError as exc:
        raise ApiError(404, "not_found", str(exc)) from exc
    except DedupConflict as exc:
        raise ApiError(409, "conflict", str(exc)) from exc
    return SplitOut(listing_id=listing_id)
