"""fetch-board (I1, I2, I5 to I7): one board's jobs into posting, then extraction queued.

Throttled to 1 start per second per ATS host, 2 retries. A board that 404s is marked
last_ok = false and the run carries on; one that keeps failing after its retries is
reported in the run's counts instead of failing the whole run. A board unfollowed while its
run was going stores nothing (I14).

A board is shared (PRD S7), so this runs as the service and always stores the whole board:
the roles a search was scoped to narrow what that user's run picks, never what is kept.
Extraction is queued only for the titles someone who follows the board wants
(pipeline/enrich.py), which is what keeps the service's AI budget for jobs that are wanted.

Because it is shared, a board fetched moments ago for one person is not fetched again for
the next (T6, JAP_BOARD_FRESH_MINUTES). What a fresh skip must *not* skip is the reading:
whoever wants a title is worked out at fetch time, so a user who followed this company
after the last fetch — or who has just added a target role — was not in that set, and
their jobs would otherwise never be read. A skip therefore still runs the wanted pass over
the board's open postings whose description has not been read yet, and queues those. Only
the HTTP call and close-missing are skipped; close-missing needs a list of what the board
listed, which a skip does not have, and nothing can have closed in the window anyway.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import inngest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import Posting, WatchedBoard
from database.session import get_sessionmaker
from functions.client import inngest_client
from pipeline.enrich import wanted_on
from pipeline.ingest import close_missing, store_jobs
from schemas.job import ATS
from sources import ADAPTERS, BoardNotFound, SourceChanged, SourceUnavailable, base

EVENTS_PER_SEND = 100
# What a fetch of a board I no longer follow returns: nothing stored, closed or queued.
UNFOLLOWED: dict[str, Any] = {
    "ok": True, "unfollowed": True, "fetched": 0, "new": 0, "changed": 0, "unchanged": 0,
    "reopened": 0, "seen": [], "to_enrich": [],
}  # fmt: skip
# A board someone else fetched a moment ago: nothing fetched or closed, but see the module
# docstring — to_enrich is filled in, because who wants what has moved on since.
FRESH: dict[str, Any] = {
    "ok": True, "fresh": True, "fetched": 0, "new": 0, "changed": 0, "unchanged": 0,
    "reopened": 0, "seen": [],
}  # fmt: skip


def _is_fresh(board: WatchedBoard, now: datetime) -> bool:
    """Whether this board's jobs were read recently enough to be used again (T6). A board
    never fetched, or whose last fetch failed, is always fetched."""
    minutes = get_settings().jap_board_fresh_minutes
    if minutes <= 0 or board.last_checked_at is None or not board.last_ok:
        return False
    return board.last_checked_at > now - timedelta(minutes=minutes)


async def _unread_wanted(session: AsyncSession, board_id: uuid.UUID) -> list[str]:
    """Open postings on this board whose description has not been read yet and that
    somebody following it now wants. This is what a fresh skip still has to queue."""
    wanted = await wanted_on(session, board_id)
    rows = await session.execute(
        select(Posting.id, Posting.title_raw).where(
            Posting.watched_board_id == board_id,
            Posting.state == "open",
            Posting.extracted_hash.is_distinct_from(Posting.description_hash),
        )
    )
    return [str(pid) for pid, title in rows.all() if wanted.title(title)]


async def _mark(board_id: uuid.UUID, ok: bool) -> None:
    async with get_sessionmaker()() as session:
        board = await session.get(WatchedBoard, board_id)
        if board is not None:
            board.last_checked_at = datetime.now(UTC)
            board.last_ok = ok
            await session.commit()


async def fetch_step(board_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        board = await session.get(WatchedBoard, uuid.UUID(board_id))
        if board is None:
            raise inngest.NonRetriableError("board not found")
        if not board.active:
            return dict(UNFOLLOWED)  # unfollowed after the run listed its boards (I14)
        if _is_fresh(board, datetime.now(UTC)):  # somebody else just fetched it (T6)
            return {**FRESH, "to_enrich": await _unread_wanted(session, uuid.UUID(board_id))}
        adapter = ADAPTERS[ATS(board.ats)]
        token, name = board.board_token, board.display_name
    try:
        async with base.client() as http:
            jobs = await adapter.fetch(http, token, name)
    except BoardNotFound:
        await _mark(uuid.UUID(board_id), ok=False)
        return {"ok": False, "error": f"{name}: board not found on {board.ats}"}
    except SourceChanged as exc:
        await _mark(uuid.UUID(board_id), ok=False)
        raise inngest.NonRetriableError(str(exc)) from exc
    except SourceUnavailable:
        raise  # retried by Inngest
    async with get_sessionmaker()() as session:
        stored = await store_jobs(session, uuid.UUID(board_id), jobs)
        if stored is None:
            return dict(UNFOLLOWED)  # unfollowed while the board was being read: discarded
        wanted = await wanted_on(session, uuid.UUID(board_id))
        changed = [uuid.UUID(pid) for pid in stored.to_enrich]
        rows = await session.execute(
            select(Posting.id, Posting.title_raw).where(Posting.id.in_(changed))
        )
        to_enrich = [str(pid) for pid, title in rows.all() if wanted.title(title)]
    await _mark(uuid.UUID(board_id), ok=True)
    return {
        "ok": True,
        "fetched": len(jobs),
        "new": stored.new,
        "changed": stored.changed,
        "unchanged": stored.unchanged,
        "reopened": stored.reopened,
        "seen": [j.external_id for j in jobs],
        "to_enrich": to_enrich,
    }


async def close_step(board_id: str, seen: list[str]) -> int:
    async with get_sessionmaker()() as session:
        return await close_missing(session, uuid.UUID(board_id), seen)


@inngest_client.create_function(
    fn_id="fetch-board",
    trigger=inngest.TriggerEvent(event="board/fetch.requested"),
    throttle=inngest.Throttle(key="event.data.host", limit=1, period=timedelta(seconds=1)),
    # One at a time per board (T6). The throttle above spaces out a host, which is about
    # being polite to the ATS; this stops two searches starting on the same board at the
    # same instant, where both would see it as not fresh and fetch it twice.
    concurrency=[inngest.Concurrency(key="event.data.board_id", limit=1)],
    retries=2,
)
async def fetch_board(ctx: inngest.Context) -> dict[str, Any]:
    board_id = str(ctx.event.data["board_id"])
    try:
        fetched = await ctx.step.run("fetch", fetch_step, board_id)
    except inngest.StepError as exc:
        return {"board_id": board_id, "ok": False, "error": exc.message or "fetch failed"}
    if not fetched["ok"]:
        return {"board_id": board_id, **fetched}
    if fetched.get("unfollowed"):
        counted = {k: 0 for k in ("fetched", "new", "changed", "unchanged", "reopened")}
        return {"board_id": board_id, "ok": True, **counted, "closed": 0, "enrich_queued": 0}
    # A fresh skip never closes anything: close-missing closes every posting not in `seen`,
    # and a skip has no list of what the board listed. Nothing closed inside the window
    # that the fetch which filled it did not already see.
    fresh = bool(fetched.get("fresh"))
    closed = (
        0 if fresh else await ctx.step.run("close-missing", close_step, board_id, fetched["seen"])
    )
    to_enrich: list[str] = fetched["to_enrich"]
    for i in range(0, len(to_enrich), EVENTS_PER_SEND):
        await ctx.step.send_event(
            f"enrich-{i // EVENTS_PER_SEND}",
            [
                inngest.Event(name="posting/enrich.requested", data={"posting_id": pid})
                for pid in to_enrich[i : i + EVENTS_PER_SEND]
            ],
        )
    return {
        "board_id": board_id,
        "ok": True,
        "fresh": fresh,
        **{k: fetched[k] for k in ("fetched", "new", "changed", "unchanged", "reopened")},
        "closed": closed,
        "enrich_queued": len(to_enrich),
    }
