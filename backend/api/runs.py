"""Runs (T3 to T5, U6): the Run dialog, the last run line and the run log, each user's
own searches only (S7)."""

import uuid
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ApiError
from database import tenant
from database.models import Posting, Run
from database.session import Session
from functions.events import send
from pipeline.runs import in_scope, queue_run, role_matcher
from pipeline.workspace import follows, profile_of
from schemas.run import RunIn, RunOut, RunState

router = APIRouter(prefix="/runs", tags=["runs"])


async def extracting(session: AsyncSession) -> int:
    """Postings still to be read by the AI for me: open ones of companies I follow (I13)
    whose title is in one of my target roles. The rest are read only if another follower
    wants them (pipeline/enrich.py), so they are not something I am waiting for."""
    uid = tenant.user_id(session)
    titles = await session.scalars(
        select(Posting.title_raw).where(
            Posting.watched_board_id.in_(follows(uid)),
            Posting.state == "open",
            Posting.extracted_hash.is_distinct_from(Posting.description_hash),
        )
    )
    profile = await profile_of(session)
    matcher = role_matcher(profile.target_roles if profile else None)
    return sum(1 for title in titles if in_scope(title, matcher))


def to_out(run: Run, pending: int = 0) -> RunOut:
    return RunOut(
        id=run.id,
        trigger=run.trigger,
        mode=run.mode,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
        scope=run.scope or {},
        counts=run.counts or {},
        error=run.error,
        extracting=pending,
    )


async def request_run(
    session: AsyncSession,
    trigger: str,
    mode: str = "find_only",
    scope: dict[str, Any] | None = None,
) -> Run:
    """Queue a run (or join the one already queued, T4) and send its event if it is new."""
    run, created = await queue_run(session, trigger, mode, scope)
    if created:
        await send("run/requested", {"run_id": str(run.id), "user_id": str(run.user_id)})
    return run


@router.post("", response_model=RunOut, status_code=202)
async def start(body: RunIn, session: Session) -> RunOut:
    scope: dict[str, Any] = {}
    if body.board_ids:
        scope["board_ids"] = [str(b) for b in body.board_ids]
    if body.roles:
        scope["roles"] = body.roles
    run = await request_run(session, "manual", body.mode, scope)
    return to_out(run, await extracting(session))


@router.get("/state", response_model=RunState)
async def state(session: Session) -> RunState:
    """The last run line (U6) plus the run going now and the one waiting behind it."""

    async def newest(*statuses: str) -> Run | None:
        query = select(Run).where(Run.user_id == uid, Run.status.in_(statuses))
        return await session.scalar(query.order_by(Run.created_at.desc()).limit(1))

    uid = tenant.user_id(session)
    pending = await extracting(session)
    last, active, queued = (
        await newest("succeeded", "failed"),
        await newest("running"),
        await newest("queued"),
    )
    return RunState(
        last=to_out(last, pending) if last else None,
        active=to_out(active, pending) if active else None,
        queued=to_out(queued) if queued else None,
    )


@router.get("/latest", response_model=RunOut | None)
async def latest(session: Session) -> RunOut | None:
    run = await session.scalar(
        select(Run)
        .where(Run.user_id == tenant.user_id(session))
        .order_by(Run.created_at.desc())
        .limit(1)
    )
    return to_out(run, await extracting(session)) if run else None


@router.get("", response_model=list[RunOut])
async def run_log(session: Session) -> list[RunOut]:
    rows = await session.scalars(
        select(Run)
        .where(Run.user_id == tenant.user_id(session))
        .order_by(Run.created_at.desc())
        .limit(50)
    )
    return [to_out(r) for r in rows]


@router.get("/{run_id}", response_model=RunOut)
async def one(run_id: uuid.UUID, session: Session) -> RunOut:
    run = await session.scalar(
        select(Run).where(Run.id == run_id, Run.user_id == tenant.user_id(session))
    )
    if run is None:
        raise ApiError(404, "not_found", "No such run")
    return to_out(run)
