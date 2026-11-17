"""Listings (U1 to U4, U11 to U13, D1 to D5, F1 to F3): one row per company and role.

A listing is shared; what it is to me is my user_listing row (PRD S7). Only listings that
match My profile are shown (user_listing.matched, written by pipeline.matching), newest
first, and only from companies I follow. A listing is hidden when none of its postings comes
from a company I still follow, unless it has an application: those stay in my history (I12),
and the panel says I stopped following the company. A listing that is not mine to see (no
company I follow has it and I have no status on it) answers Not Found. Merge changes the
shared listing for everyone, so it is the admin's. The filter bar narrows by role, location,
status and matched
condition without touching the profile (F2). Skills never hide a job: each row carries its
skills match, which the list can be sorted by and filtered on with a minimum (F5).
"""

import uuid
from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Query
from sqlalchemy import ColumnElement, Text, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from api.applications import to_out as application_out
from api.errors import ApiError
from api.postings import detail
from auth.deps import AdminUser
from database import tenant
from database.models import Application, Company, Listing, Posting, UserListing
from database.session import Session
from functions.events import send
from pipeline.apply.flow import ACTIVE, MAYBE_SENT, maybe_sent, why_not
from pipeline.dedup import DedupConflict, merge_listings
from pipeline.matching import refilter_users
from pipeline.unfollow import IN_HISTORY
from pipeline.workspace import can_see, followed, is_open, profile_of, set_status, state_of
from schemas.application import ApplyQueued, RetryIn
from schemas.job import ATS
from schemas.listing import (
    AlsoOn,
    ListingDetail,
    ListingPage,
    ListingRef,
    ListingRow,
    ListingSummary,
    MergeIn,
    RoleCounts,
    SkillsMatch,
    StatusCounts,
)

STATUSES = (
    "not_applied", "ready_for_review", "applying", "applied", "needs_input", "failed",
    "manual_apply", "skipped", "closed",
)  # fmt: skip
STATUS_FILTER = "^(open|active|all|" + "|".join(STATUSES) + ")$"
OTHER_ROLE = "__other"  # role filter value for listings matched through Location only
SORTS = {
    "title": Listing.title_display,
    "company": Company.name,
    "location": func.array_to_string(Listing.locations, " | "),
    "work_mode": cast(Listing.work_mode, Text),
    "status": cast(UserListing.status, Text),
    "posted": Listing.last_posted_at,
    "skills": UserListing.required_pct,  # then nice to have; unknown last either way (F5)
}

router = APIRouter(prefix="/listings", tags=["listings"])


def mine(user_id: uuid.UUID) -> ColumnElement[bool]:
    """The join from a listing to this user's row for it."""
    return and_(UserListing.listing_id == Listing.id, UserListing.user_id == user_id)


async def postings_of(
    session: AsyncSession, ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[Posting]]:
    out: dict[uuid.UUID, list[Posting]] = defaultdict(list)
    if ids:
        for p in await session.scalars(select(Posting).where(Posting.listing_id.in_(ids))):
            assert p.listing_id is not None
            out[p.listing_id].append(p)
    return out


def platforms(postings: list[Posting]) -> list[ATS]:
    found = {p.source for p in postings if p.state == "open"} or {p.source for p in postings}
    return [ATS(s) for s in sorted(found)]


async def my_skills(session: AsyncSession) -> tuple[list[str], list[str]]:
    """My required and nice-to-have skills, the totals the stored skills match is out of."""
    profile = await profile_of(session)
    if profile is None:
        return [], []
    must = list(profile.must_have_skills)
    return must, [s for s in profile.nice_to_have_skills if s not in must]


def skills_match(
    listing: Listing, state: UserListing | None, skills: tuple[list[str], list[str]]
) -> SkillsMatch:
    must, nice = skills
    stated = bool(listing.skills)
    required_hit = state.required_hit if state else []
    hit = [s for s in must if s in required_hit]
    return SkillsMatch(
        stated=stated,
        required_hit=hit,
        required_missing=[s for s in must if s not in hit] if stated else [],
        required_total=len(must),
        required_pct=state.required_pct if state and stated and must else None,
        nice_hit=[s for s in nice if s in (state.nice_hit if state else [])],
        nice_total=len(nice),
    )


def to_row(
    listing: Listing,
    state: UserListing | None,
    status: str,
    company: str,
    postings: list[Posting],
    skills: tuple[list[str], list[str]],
) -> ListingRow:
    match = skills_match(listing, state, skills)
    return ListingRow(
        id=listing.id,
        title=listing.title_display,
        company=company,
        locations=listing.locations,
        work_mode=listing.work_mode,
        status=status,
        last_posted_at=listing.last_posted_at,
        found_at=listing.created_at,
        platforms=platforms(postings),
        postings=len(postings),
        role=listing.canonical_role,
        matched=(state.matched if state else None) or [],
        matched_role=state.matched_role if state else None,
        nice_skills=match.nice_hit,
        skills_match=match,
    )


def listed(user_id: uuid.UUID) -> list[ColumnElement[bool]]:
    return [
        UserListing.matched.is_not(None),
        or_(followed(user_id), UserListing.status.in_(IN_HISTORY)),
    ]


@router.get("", response_model=ListingPage)
async def listings(
    session: Session,
    status: str = Query("open", pattern=STATUS_FILTER),
    role: str | None = Query(None, max_length=200),
    location: str | None = Query(None, max_length=100),
    matched: str | None = Query(None, pattern="^(role|location)$"),
    min_skills: int | None = Query(None, ge=0, le=100),
    q: str | None = Query(None, max_length=100),
    work_mode: str | None = Query(None, pattern="^(remote|hybrid|onsite|unknown)$"),
    posted_days: int | None = Query(None, ge=1, le=365),
    sort: str = Query("posted", pattern="^(" + "|".join(SORTS) + ")$"),
    dir: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> ListingPage:
    uid = tenant.user_id(session)
    where = listed(uid)
    if status == "open":
        where.append(UserListing.status != "closed")
    elif status == "active":  # All open on Jobs: still to do, so not Sent, hidden or Closed
        where.append(UserListing.status.not_in(("closed", "skipped", "applied")))
    elif status != "all":
        where.append(UserListing.status == status)
    if role == OTHER_ROLE:
        where.append(UserListing.matched_role.is_(None))
    elif role:
        where.append(UserListing.matched_role == role)
    if location:
        where.append(func.array_to_string(Listing.locations, " | ").ilike(f"%{location.strip()}%"))
    if matched:
        where.append(UserListing.matched.contains(cast([matched], ARRAY(Text))))
    if min_skills:  # at least this % of my required skills; unknown is not a pass (F5)
        where.append(UserListing.required_pct >= min_skills)
    if work_mode == "unknown":
        where.append(Listing.work_mode.is_(None))
    elif work_mode:
        where.append(cast(Listing.work_mode, Text) == work_mode)
    if posted_days:
        where.append(Listing.last_posted_at >= func.now() - timedelta(days=posted_days))
    if q:
        like = f"%{q.strip()}%"
        where.append(
            or_(
                Listing.title_display.ilike(like),
                Company.name.ilike(like),
                func.array_to_string(Listing.locations, " | ").ilike(like),
            )
        )
    base = (
        select(Listing, UserListing, Company.name)
        .join(UserListing, mine(uid))
        .join(Company, Company.id == Listing.company_id)
    )
    key = SORTS[sort]
    order = [(key.desc() if dir == "desc" else key.asc()).nulls_last()]
    if sort == "skills":  # required %, then nice to have, then newest
        nice_n = func.cardinality(UserListing.nice_hit)
        order += [nice_n.desc() if dir == "desc" else nice_n.asc()]
        order += [Listing.last_posted_at.desc().nulls_last()]
    total = await session.scalar(
        select(func.count())
        .select_from(Listing)
        .join(UserListing, mine(uid))
        .join(Company, Company.id == Listing.company_id)
        .where(*where)
    )
    rows = (
        await session.execute(
            base.where(*where)
            .order_by(*order, Listing.title_display, Listing.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    by_listing = await postings_of(session, [listing.id for listing, _, _ in rows])
    skills = await my_skills(session)
    return ListingPage(
        total=int(total or 0),
        page=page,
        page_size=page_size,
        rows=[
            to_row(listing, state, state.status, name, by_listing[listing.id], skills)
            for listing, state, name in rows
        ],
    )


@router.get("/summary", response_model=ListingSummary)
async def summary(session: Session) -> ListingSummary:
    """Totals and Jobs by role over the listed, not Closed listings (U1, U2). Each listing
    counts once, under the first target role it matches; the rest count under Other."""
    uid = tenant.user_id(session)
    rows = await session.execute(
        select(UserListing.matched_role, UserListing.status, func.count())
        .select_from(Listing)
        .join(UserListing, mine(uid))
        .where(*listed(uid), UserListing.status != "closed")
        .group_by(UserListing.matched_role, UserListing.status)
    )
    profile = await profile_of(session)
    roles: list[str | None] = [*(profile.target_roles if profile else []), None]
    totals = StatusCounts()
    by_role: dict[str | None, RoleCounts] = {r: RoleCounts(role=r) for r in roles}
    for role, status, n in rows.all():
        counts = by_role.setdefault(role, RoleCounts(role=role))
        for c in (totals, counts):
            c.listings += n
            setattr(c, status, getattr(c, status) + n)
    each = (
        select(func.unnest(Listing.locations).label("loc"))
        .join(UserListing, mine(uid))
        .where(*listed(uid), UserListing.status != "closed")
        .subquery()
    )
    places = await session.execute(
        select(each.c.loc, func.count())
        .group_by(each.c.loc)
        .order_by(func.count().desc(), each.c.loc)
        .limit(40)
    )
    return ListingSummary(
        totals=totals, by_role=list(by_role.values()), locations=[p for p, _ in places.all()]
    )


async def load_detail(session: AsyncSession, listing_id: uuid.UUID) -> ListingDetail:
    uid = tenant.user_id(session)
    listing = await session.get(Listing, listing_id)
    if listing is None or not await can_see(session, listing_id):
        raise ApiError(404, "not_found", "No such listing")
    company = await session.get(Company, listing.company_id)
    assert company is not None
    state = await state_of(session, listing_id)
    open_to_me = await is_open(session, listing_id, uid)
    status = state.status if state else ("not_applied" if open_to_me else "closed")
    postings = (await postings_of(session, [listing.id]))[listing.id]
    primary = next((p for p in postings if p.id == listing.primary_posting_id), None)
    others = sorted(
        (p for p in postings if p.id != listing.primary_posting_id),
        key=lambda p: (p.state != "open", p.source, p.title_raw),
    )
    attempts = list(
        await session.scalars(
            select(Application)
            .where(Application.listing_id == listing.id, Application.user_id == uid)
            .order_by(Application.created_at.desc())
        )
    )
    same = await session.execute(
        select(Listing.id, Listing.title_display)
        .where(Listing.company_id == listing.company_id, Listing.id != listing.id, followed(uid))
        .order_by(Listing.title_display)
    )
    return ListingDetail(
        **to_row(
            listing, state, status, company.name, postings, await my_skills(session)
        ).model_dump(),
        skills=listing.skills,
        seniority=listing.seniority,
        remote_regions=listing.remote_regions,
        primary=detail(primary) if primary else None,
        also_on=[
            AlsoOn(
                posting_id=p.id,
                source=ATS(p.source),
                title=p.title_raw,
                company=p.company_name_raw,
                locations=p.locations,
                posting_url=p.posting_url,
                state=p.state,
                posted_at=p.posted_at,
            )
            for p in others
        ],
        same_company=[ListingRef(id=i, title=t) for i, t in same.all()],
        application=await application_out(session, attempts[0]) if attempts else None,
        attempts=len(attempts),
        apply_blocked=await why_not(session, listing.id),
        unfollowed=not await session.scalar(
            select(followed(uid)).select_from(Listing).where(Listing.id == listing.id)
        ),
    )


@router.get("/{listing_id}", response_model=ListingDetail)
async def listing(listing_id: uuid.UUID, session: Session) -> ListingDetail:
    return await load_detail(session, listing_id)


@router.post("/{listing_id}/merge", response_model=ListingDetail)
async def merge(
    listing_id: uuid.UUID, body: MergeIn, admin: AdminUser, session: Session
) -> ListingDetail:
    """Merge the other listing into this one (D5); remembered on later runs. The listing is
    shared, so this is the admin's call and it changes everyone's list (PRD S7)."""
    try:
        await merge_listings(session, listing_id, body.other_listing_id)
        await refilter_users(session, [listing_id])
        await session.commit()
    except LookupError as exc:
        raise ApiError(404, "not_found", str(exc)) from exc
    except DedupConflict as exc:
        raise ApiError(409, "conflict", str(exc)) from exc
    await tenant.use(session, tenant.user(admin.id))
    return await load_detail(session, listing_id)


@router.post("/{listing_id}/apply", response_model=ApplyQueued, status_code=202)
async def apply_now(
    listing_id: uuid.UUID, session: Session, body: RetryIn | None = None
) -> ApplyQueued:
    """Apply now (one listing): prepares it; in Review mode it stops at Ready for review."""
    if not await can_see(session, listing_id):
        raise ApiError(404, "not_found", "No such listing")
    reason = await why_not(session, listing_id)
    if reason:
        raise ApiError(409, "conflict", reason)
    if await maybe_sent(session, listing_id) and not (body and body.checked):
        raise ApiError(409, "submit_maybe_sent", MAYBE_SENT)
    await send(
        "listing/apply.requested",
        {"listing_id": str(listing_id), "run_id": None, "user_id": str(tenant.user_id(session))},
    )
    return ApplyQueued(listing_id=listing_id, queued=True)


@router.post("/{listing_id}/skip", response_model=ListingDetail)
async def skip(listing_id: uuid.UUID, session: Session) -> ListingDetail:
    """Skip: I chose not to apply. An attempt waiting for me is cancelled."""
    uid = tenant.user_id(session)
    if not await can_see(session, listing_id):
        raise ApiError(404, "not_found", "No such listing")
    state = await state_of(session, listing_id, lock=True)
    if state is not None and state.status == "applied":
        raise ApiError(409, "conflict", "This job is Applied")
    active = list(
        await session.scalars(
            select(Application)
            .where(
                Application.listing_id == listing_id,
                Application.user_id == uid,
                Application.status.in_(ACTIVE),
            )
            .with_for_update()
        )
    )
    if any(a.status == "applying" for a in active):
        raise ApiError(409, "conflict", "JAP is filling or submitting this form right now")
    for a in active:
        a.status = "cancelled"
    await set_status(session, listing_id, "skipped")
    await session.commit()
    if active:
        await send("application/cancelled", {"listing_id": str(listing_id), "user_id": str(uid)})
    return await load_detail(session, listing_id)


@router.post("/{listing_id}/unskip", response_model=ListingDetail)
async def unskip(listing_id: uuid.UUID, session: Session) -> ListingDetail:
    """Undo Skip: back to Not applied (or Closed if every posting closed meanwhile)."""
    state = await state_of(session, listing_id, lock=True)
    if state is None:
        raise ApiError(404, "not_found", "No such listing")
    if state.status != "skipped":
        raise ApiError(409, "conflict", "This job is not Skipped")
    open_to_me = await is_open(session, listing_id, tenant.user_id(session))
    state.status = "not_applied" if open_to_me else "closed"
    await session.commit()
    return await load_detail(session, listing_id)
