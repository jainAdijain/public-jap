"""run-pipeline (T4, T5, I7, D6, F1): one run. fetch, dedup (the model settles borderline
pairs), then filter (the match rule
on every stored listing), and for Find and apply, pick: one listing/apply.requested per
passing Not applied listing, within today's cap (sent, not invoked, so a listing waiting
days for review never holds the run open). Runs are started by the Run dialog, the Daily
run, a profile save and an accepted resume (functions/triggers.py).

A run is one user's (PRD S7, S13): it fetches the boards they follow, and filter, pick and
the caps are theirs. The shared steps in between (start, which looks at everyone's runs,
then fetch, dedup and the model settling pairs) run as the service, and filter then
re-filters everyone who follows one of the boards just fetched, so a second follower's list
is as fresh as the first's without a fetch of their own.

Concurrency 1 across the app, so a queued run waits for the active one. Each board is
fetched by its own fetch-board run, invoked in parallel. Dedup waits (a few minutes at
most) for new postings' embeddings, which Ollama makes in seconds, so near matches count.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import inngest
from sqlalchemy import select

from config import get_settings
from database import tenant
from database.models import BoardFollow, Run
from database.session import get_sessionmaker
from functions.client import inngest_client
from functions.fetch_board import fetch_board
from llm.ollama import ping_ollama
from llm.spend import month_spend
from pipeline.apply.flow import applied_today, daily_cap, pick
from pipeline.dedup import dedup, settle_duplicates
from pipeline.matching import listed_count, refilter_users
from pipeline.runs import embeddings_pending, finish_run, start_run
from schemas.job import ATS
from sources import ADAPTERS

WAIT_ROUNDS, WAIT_SECONDS = 24, 5  # up to two minutes for embeddings
TURN_ROUNDS, TURN_SECONDS = 360, 10  # up to an hour waiting for the run before this one
COUNTED = ("fetched", "new", "changed", "unchanged", "reopened", "closed", "enrich_queued")


async def start_step(run_id: str) -> list[dict[str, Any]] | None:
    with tenant.as_system():  # one run at a time across the service: it reads everyone's
        async with get_sessionmaker()() as session:
            return await start_run(session, uuid.UUID(run_id))


async def pending_step(run_id: str) -> int:
    """Postings waiting for an embedding, or 0 when Ollama is down (no point waiting)."""
    settings = get_settings()
    if not (await ping_ollama(settings.ollama_base_url, settings.jap_model_embed)).ok:
        return 0
    with tenant.as_system():
        async with get_sessionmaker()() as session:
            return await embeddings_pending(session, uuid.UUID(run_id))


async def dedup_step(run_id: str) -> dict[str, Any]:
    with tenant.as_system():  # listings are shared
        async with get_sessionmaker()() as session:
            run = await session.get(Run, uuid.UUID(run_id))
            since = run.started_at if run is not None else datetime.now(UTC)
            counts = await dedup(session, since)
    return counts.as_dict()


async def settle_step() -> dict[str, int]:
    """The model decides every borderline pair: merged, kept apart, or left for the next
    run. Billed to the service: a pair of shared listings is nobody's in particular."""
    with tenant.as_system():
        async with get_sessionmaker()() as session:
            settled = await settle_duplicates(session)
    return {"ai_merged": settled.merged, "ai_kept_apart": settled.kept_apart,
            "ai_undecided": settled.undecided}  # fmt: skip


async def filter_step(run_id: str) -> dict[str, int]:
    """Re-filter everyone who follows a board this run fetched, and count the run's user."""
    with tenant.as_system():
        async with get_sessionmaker()() as session:
            run = await session.get(Run, uuid.UUID(run_id))
            if run is None:
                return {"listed": 0}
            boards = select(BoardFollow.board_id).where(
                BoardFollow.user_id == run.user_id, BoardFollow.active
            )
            await refilter_users(session, board_ids=boards)
            listed = await listed_count(session, run.user_id)
            await session.commit()
    return {"listed": listed}


async def pick_step(run_id: str) -> list[str]:
    async with get_sessionmaker()() as session:
        return await pick(session, run_id)


async def limits_step() -> dict[str, Any]:
    """The caps that held this run back, for the Runs card, both its user's own:
    spend_cap_reached when this month's model spend is at their allowance (model steps
    stop), daily_cap_reached when today's applications are at the daily cap (A1; nothing
    more is submitted until midnight)."""
    async with get_sessionmaker()() as session:
        spend = await month_spend(session)
        cap = await daily_cap(session)
        today = await applied_today(session, datetime.now(UTC))
    out: dict[str, Any] = {}
    if spend.reached:
        out["spend_cap_reached"] = True
    if today >= cap:
        out["daily_cap_reached"] = True
    return out


async def finish_step(run_id: str, counts: dict[str, Any], error: str | None) -> None:
    async with get_sessionmaker()() as session:
        await finish_run(session, uuid.UUID(run_id), counts, error)


async def _failed(ctx: inngest.Context) -> None:
    original = ctx.event.data.get("event")
    data = original.get("data") if isinstance(original, dict) else None
    run_id = data.get("run_id") if isinstance(data, dict) else None
    user_id = data.get("user_id") if isinstance(data, dict) else None
    error = ctx.event.data.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    if isinstance(run_id, str):
        await finish_step(run_id, {}, message or "Run failed after retries")
        await inngest_client.send(
            inngest.Event(name="run/failed", data={"run_id": run_id, "user_id": user_id})
        )


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, Any] = {k: 0 for k in COUNTED}
    counts["boards"] = len(results)
    counts["boards_failed"] = 0
    errors: list[str] = []
    for r in results:
        if not r.get("ok"):
            counts["boards_failed"] += 1
            errors.append(str(r.get("error") or "fetch failed"))
            continue
        for k in COUNTED:
            counts[k] += int(r.get(k) or 0)
    if errors:
        counts["board_errors"] = errors[:20]
    return counts


@inngest_client.create_function(
    fn_id="run-pipeline",
    trigger=inngest.TriggerEvent(event="run/requested"),
    concurrency=[inngest.Concurrency(limit=1)],
    retries=2,
    on_failure=_failed,
)
async def run_pipeline(ctx: inngest.Context) -> dict[str, Any]:
    run_id = str(ctx.event.data["run_id"])
    user_id = str(ctx.event.data["user_id"])
    boards = await ctx.step.run("start", start_step, run_id)
    for i in range(TURN_ROUNDS):  # one run at a time (T4); see pipeline/runs.py
        if boards is not None:
            break
        await ctx.step.sleep(f"wait-turn-{i}", timedelta(seconds=TURN_SECONDS))
        boards = await ctx.step.run(f"start-{i}", start_step, run_id)
    if boards is None:
        raise inngest.NonRetriableError("The run before this one did not finish within an hour")

    def fetch(board: dict[str, Any]) -> Any:
        async def go() -> dict[str, Any]:
            # No user_id: a board is shared, so its fetch runs as the service.
            data = {"run_id": run_id, "board_id": board["board_id"],
                    "host": ADAPTERS[ATS(board["ats"])].host}  # fmt: skip
            try:
                return await ctx.step.invoke(
                    f"fetch-{board['board_id']}", function=fetch_board, data=data
                )
            except inngest.StepError as exc:
                return {"board_id": board["board_id"], "ok": False, "error": exc.message}

        return go

    results = list(await ctx.group.parallel(tuple(fetch(b) for b in boards))) if boards else []
    counts = summarize(results)
    for i in range(WAIT_ROUNDS):
        if not await ctx.step.run(f"embeddings-pending-{i}", pending_step, run_id):
            break
        await ctx.step.sleep(f"wait-embeddings-{i}", timedelta(seconds=WAIT_SECONDS))
    counts.update(await ctx.step.run("dedup", dedup_step, run_id))
    settled = await ctx.step.run("settle-duplicates", settle_step)
    counts["merged"] = int(counts.get("merged") or 0) + settled["ai_merged"]
    counts.update(settled)
    counts.update(await ctx.step.run("filter", filter_step, run_id))
    picked = await ctx.step.run("pick", pick_step, run_id)
    if picked:
        await ctx.step.send_event(
            "apply",
            [
                inngest.Event(
                    name="listing/apply.requested",
                    data={"listing_id": lid, "run_id": run_id, "user_id": user_id},
                )
                for lid in picked
            ],
        )
    counts["apply_queued"] = len(picked)
    counts.update(await ctx.step.run("limits", limits_step))
    await ctx.step.run("finish", finish_step, run_id, counts, None)
    if counts["boards_failed"]:
        await ctx.step.send_event(
            "send-failed",
            inngest.Event(name="run/failed", data={"run_id": run_id, "user_id": user_id}),
        )
    return counts
