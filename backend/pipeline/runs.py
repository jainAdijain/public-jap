"""Creating runs (T4, T6): a run row exists before its event is sent, and repeats collapse.

The one_queued_run index allows each user a single queued run. A trigger that finds one
reuses it instead of adding another, widening its scope to cover both asks.

**Searches go side by side, one per user.** Two people searching at the same time both
start; one person's second search waits for their first. run-pipeline carries that as two
concurrency entries, keyed on the event's user_id and capped service-wide by
JAP_MAX_PARALLEL_RUNS (functions/run_pipeline.py). Until v0.20.0 there was a single limit
of 1 and everybody queued behind whoever searched first.

Inngest's concurrency limit counts executing steps, not runs: a run waiting on its board
fetches does not hold the slot, so a second run of the same user could start beside it. So
a run claims its turn itself — start_run returns None while *that user's* other run is
going — and keeps waiting as queued, which also keeps it the run that later triggers join.

A scope (the Run dialog, T3) narrows a run to some boards and some roles. A board is
always fetched and stored whole, because its postings are shared and another follower may
want the rest (PRD S7); the roles narrow what the run picks to apply for, matched the way
the match rule matches them (whole words, Sr to Senior, engineer = developer).
"""

import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import tenant
from database.models import AppUser, BoardFollow, Posting, Run, WatchedBoard
from pipeline.canonical import canonical_role
from pipeline.matching import role_pattern

SCOPE_KEYS = ("board_ids", "roles")
# A run still marked running after this long is taken as crashed and no longer blocks.
STALE_RUN = timedelta(hours=2)


def merge_scope(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """The scope covering both: a key missing on either side means everything."""
    out: dict[str, Any] = {}
    for key in SCOPE_KEYS:
        if a.get(key) and b.get(key):
            out[key] = list(dict.fromkeys([*a[key], *b[key]]))
    return out


def role_matcher(roles: Iterable[str] | None) -> re.Pattern[str] | None:
    """A regex for a job title in one of these roles, or None when every role counts."""
    patterns = [p for r in roles or () if (p := role_pattern(r))]
    return re.compile("|".join(f"(?:{p})" for p in patterns)) if patterns else None


def in_scope(title: str, matcher: re.Pattern[str] | None) -> bool:
    return matcher is None or bool(matcher.search(canonical_role(title) or title.lower()))


async def queue_run(
    session: AsyncSession, trigger: str, mode: str, scope: dict[str, Any] | None = None
) -> tuple[Run, bool]:
    """The session user's queued run to use, and whether it is new (only a new one needs
    its event sent)."""
    uid = tenant.user_id(session)
    mine = select(Run).where(Run.user_id == uid, Run.status == "queued")
    queued = await session.scalar(mine)
    if queued is not None:
        merged = merge_scope(queued.scope or {}, scope or {})
        upgrade = mode == "find_and_apply" and queued.mode != mode  # applying covers finding
        if merged != (queued.scope or {}) or upgrade:
            queued.scope = merged
            if upgrade:
                queued.mode = mode
            await session.commit()
            await session.refresh(queued)
        return queued, False
    run = Run(user_id=uid, trigger=trigger, mode=mode, scope=scope or {})
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:  # another trigger queued one a moment ago
        await session.rollback()
        queued = await session.scalar(mine)
        assert queued is not None
        return queued, False
    await session.refresh(run)
    return run, True


async def start_run(session: AsyncSession, run_id: uuid.UUID) -> list[dict[str, Any]] | None:
    """Mark the run running and list the boards it fetches: the ones its user follows (I8).
    None while *this user's* other run is going: that one stays queued and tries again
    (T4, T6). Somebody else searching at the same time does not hold it up; the ceiling on
    how many do at once is Inngest's (JAP_MAX_PARALLEL_RUNS). Run as the service, which is
    what lets it read the run row before the user is known."""
    run = await session.get(Run, run_id, with_for_update=True)
    if run is None:
        raise LookupError(f"run {run_id} not found")
    owner = await session.get(AppUser, run.user_id)
    if owner is None or owner.disabled_at is not None:  # turned off since it was queued (S12)
        run.status, run.ended_at = "failed", datetime.now(UTC)
        run.error = "This account is turned off"
        await session.commit()
        return []
    if run.status == "queued":
        other = await session.scalar(
            select(Run.id).where(
                Run.status == "running",
                Run.user_id == run.user_id,  # T6: only my own search holds mine up
                Run.id != run_id,
                Run.started_at > datetime.now(UTC) - STALE_RUN,
            )
        )
        if other is not None:
            await session.rollback()
            return None
        run.status = "running"
        run.started_at = datetime.now(UTC)
    query = (
        select(WatchedBoard)
        .join(BoardFollow, BoardFollow.board_id == WatchedBoard.id)
        .where(BoardFollow.user_id == run.user_id, BoardFollow.active)
        .where(WatchedBoard.active, WatchedBoard.origin != "demo")  # demo boards hold fixtures
        .order_by(WatchedBoard.display_name)
    )
    board_ids = (run.scope or {}).get("board_ids")
    if board_ids:
        query = query.where(WatchedBoard.id.in_([uuid.UUID(b) for b in board_ids]))
    boards = list(await session.scalars(query))
    await session.commit()
    return [{"board_id": str(b.id), "ats": b.ats} for b in boards]


async def finish_run(
    session: AsyncSession, run_id: uuid.UUID, counts: dict[str, Any], error: str | None = None
) -> None:
    run = await session.get(Run, run_id, with_for_update=True)  # apply-listing adds counts too
    if run is None or run.status in ("succeeded", "failed"):
        return
    run.status = "failed" if error else "succeeded"
    run.ended_at = datetime.now(UTC)
    run.counts = {**(run.counts or {}), **counts}
    run.error = error
    await session.commit()


async def embeddings_pending(session: AsyncSession, run_id: uuid.UUID | None = None) -> int:
    """Open postings still waiting for their embedding (near-match dedup needs it): those
    of the boards this run's user follows, or of every followed board."""
    owner = select(Run.user_id).where(Run.id == run_id).scalar_subquery()
    boards = select(BoardFollow.board_id).where(BoardFollow.active)
    if run_id is not None:
        boards = boards.where(BoardFollow.user_id == owner)
    count = await session.scalar(
        select(func.count())
        .select_from(Posting)
        .join(WatchedBoard, WatchedBoard.id == Posting.watched_board_id)
        .where(
            WatchedBoard.active,
            WatchedBoard.id.in_(boards),
            Posting.state == "open",
            Posting.embedded_hash.is_distinct_from(Posting.description_hash),
        )
    )
    return int(count or 0)
