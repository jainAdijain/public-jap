"""Phase 5 triggers: the Run dialog's scope, repeats collapsing, the Daily run and the
profile save's background run (T1 to T5)."""

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

from database import tenant
from database.session import get_sessionmaker
from functions.fetch_board import close_step, fetch_step
from functions.triggers import daily_due, daily_step, queue_step
from pipeline.runs import finish_run, in_scope, merge_scope, role_matcher, start_run
from tests import boards as b
from tests.test_ingest import _board

GH = "https://boards-api.greenhouse.io/v1/boards"
IST = ZoneInfo("Asia/Kolkata")


def test_merge_scope_widens() -> None:
    assert merge_scope({"roles": ["Backend Engineer"]}, {"roles": ["Data Engineer"]}) == {
        "roles": ["Backend Engineer", "Data Engineer"]
    }
    assert merge_scope({"roles": ["Backend Engineer"]}, {}) == {}  # one ask is for everything
    assert merge_scope({}, {}) == {}


def test_role_scope_matches_like_the_match_rule() -> None:
    m = role_matcher(["Backend Engineer"])
    assert in_scope("Sr. Backend Developer (Remote) - Bangalore", m)
    assert in_scope("Senior Backend Engineer", m)
    assert not in_scope("Frontend Engineer", m)
    assert not in_scope("Backend Engineering Manager", m)
    assert role_matcher(None) is None and in_scope("Anything", None)


def test_daily_due() -> None:
    at = time(6, 0)
    six = datetime(2026, 10, 29, 6, 0, tzinfo=IST)
    assert daily_due(six, at, None)
    assert daily_due(six + timedelta(minutes=3), at, None)
    assert not daily_due(six - timedelta(minutes=1), at, None)
    # Missed while the Mac slept: not caught up later (v1 accepts a missed run).
    assert not daily_due(six + timedelta(minutes=30), at, None)
    # Fires once per slot, even when the next tick is still inside the window.
    assert not daily_due(six + timedelta(minutes=1), at, six)
    assert daily_due(six, at, six - timedelta(days=1))


async def test_run_dialog_scope_and_collapse(
    api: httpx.AsyncClient, events: list[tuple[str, dict[str, Any]]]
) -> None:
    first = await api.post("/api/runs", json={"roles": ["Backend Engineer", " ", ""]})
    assert first.status_code == 202
    assert first.json()["scope"] == {"roles": ["Backend Engineer"]}
    second = await api.post("/api/runs", json={"roles": ["Data Engineer"]})
    assert second.json()["id"] == first.json()["id"]  # one queued run (T4)
    assert second.json()["scope"] == {"roles": ["Backend Engineer", "Data Engineer"]}
    third = await api.post("/api/runs", json={})
    assert third.json()["id"] == first.json()["id"] and third.json()["scope"] == {}
    assert [e[0] for e in events] == ["run/requested"]

    state = (await api.get("/api/runs/state")).json()
    assert state["last"] is None and state["active"] is None
    assert state["queued"]["id"] == first.json()["id"]


async def test_state_shows_last_active_and_queued(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    sql.execute(
        "INSERT INTO run (trigger, mode, status, created_at, ended_at, counts) VALUES "
        "('scheduled', 'find_only', 'succeeded', now() - interval '2 hours', "
        " now() - interval '119 minutes', '{\"fetched\": 12, \"listed\": 3}'),"
        "('profile_saved', 'find_only', 'running', now() - interval '1 minute', null, '{}')"
    )
    queued = (await api.post("/api/runs", json={})).json()
    state = (await api.get("/api/runs/state")).json()
    assert state["last"]["trigger"] == "scheduled" and state["last"]["counts"]["listed"] == 3
    assert state["active"]["trigger"] == "profile_saved"
    assert state["queued"]["id"] == queued["id"] and queued["trigger"] == "manual"


async def test_a_scoped_run_still_stores_the_whole_board(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A board is shared (PRD S7): whatever roles a search was scoped to, the fetch stores
    and closes the whole board, because someone else who follows it may want the rest. The
    roles narrow what that run picks to apply for (pipeline/apply/flow.py)."""
    backend = b.job(b.GREENHOUSE_JOB, id=2001, title="Senior Backend Engineer")
    policy = b.GREENHOUSE_JOB  # Commercial Policy Lead
    routes: b.Routes = {f"{GH}/discord/jobs": b.greenhouse(backend, policy)}
    b.fake_boards(monkeypatch, routes)
    board_id = await _board()
    sql.execute(
        "INSERT INTO run (trigger, mode, scope) VALUES "
        "('manual', 'find_only', '{\"roles\": [\"Backend Engineer\"]}')"
    )
    row = sql.execute("SELECT id FROM run").fetchone()
    assert row is not None
    async with get_sessionmaker()() as session:
        boards = await start_run(session, row[0])
    assert boards == [{"board_id": board_id, "ats": "greenhouse"}]  # no roles go to the fetch
    assert sql.execute("SELECT status FROM run").fetchone() == ("running",)

    fetched = await fetch_step(board_id)
    assert fetched["fetched"] == 2 and fetched["new"] == 2
    assert sql.execute("SELECT count(*) FROM posting").fetchone() == (2,)

    # Both leave the board: both close, not only the scoped role's.
    routes[f"{GH}/discord/jobs"] = b.greenhouse()
    fetched = await fetch_step(board_id)
    assert await close_step(board_id, fetched["seen"]) == 2
    states = dict(sql.execute("SELECT title_raw, state FROM posting").fetchall())
    assert states == {"Senior Backend Engineer": "closed", "Commercial Policy Lead": "closed"}


async def test_a_run_fetches_only_boards_its_user_follows(
    db: str, sql: psycopg.Connection[Any], other_id: uuid.UUID
) -> None:
    mine_board = await _board()
    with tenant.as_user(other_id):
        theirs = await _board(token="stripe")
    sql.execute("INSERT INTO run (trigger, mode) VALUES ('manual', 'find_only')")
    sql.execute(
        "INSERT INTO run (user_id, trigger, mode) VALUES (%s, 'manual', 'find_only')", (other_id,)
    )
    runs = dict(sql.execute("SELECT user_id, id FROM run").fetchall())
    with tenant.as_system():
        async with get_sessionmaker()() as session:
            first = await start_run(session, runs[other_id])
            await finish_run(session, runs[other_id], {})
            second = await start_run(session, next(r for u, r in runs.items() if u != other_id))
    assert [x["board_id"] for x in first or []] == [theirs]
    assert [x["board_id"] for x in second or []] == [mine_board]


async def test_profile_save_queues_a_background_run(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], events: list[tuple[str, dict[str, Any]]]
) -> None:
    await api.put("/api/profile", json={"target_roles": ["Backend Engineer"]})
    me = (await api.get("/api/auth/me")).json()["id"]
    assert events[-1] == ("profile/saved", {"user_id": me})  # the save itself re-filtered (T2)
    [run] = await queue_step("profile_saved")
    assert run["user_id"] == me
    assert await queue_step("profile_saved") == []  # repeats collapse (T4)
    assert sql.execute("SELECT trigger FROM run").fetchall() == [("profile_saved",)]


async def test_daily_step_fires_once_when_on(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    now = datetime.now(IST)
    await api.patch("/api/profile", json={"daily_run_at": now.strftime("%H:%M")})
    assert await daily_step() == []  # Daily run is off
    await api.patch("/api/profile", json={"daily_run": True})
    [run] = await daily_step()
    assert run["user_id"] == (await api.get("/api/auth/me")).json()["id"]
    assert await daily_step() == []  # the same slot does not fire twice
    assert sql.execute("SELECT trigger, status FROM run").fetchall() == [("scheduled", "queued")]
    last = sql.execute("SELECT daily_run_last_at FROM profile").fetchone()
    assert last is not None and last[0] <= datetime.now(UTC)


async def test_the_daily_search_goes_by_each_accounts_own_zone(
    api: httpx.AsyncClient, other: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """Two accounts asking for the same wall-clock hour in different zones (U22): only the
    one whose zone says it is that time now fires."""
    kolkata = datetime.now(IST)
    hawaii = datetime.now(ZoneInfo("Pacific/Honolulu"))
    assert kolkata.strftime("%H:%M") != hawaii.strftime("%H:%M")  # the zones differ by hours
    for client, at, tz in (
        (api, kolkata, "Asia/Kolkata"),
        (other, kolkata, "Pacific/Honolulu"),  # the same clock time, read in another zone
    ):
        resp = await client.patch(
            "/api/profile", json={"daily_run": True, "daily_run_at": at.strftime("%H:%M"), "tz": tz}
        )
        assert resp.status_code == 200, resp.text
    me = (await api.get("/api/auth/me")).json()["id"]
    assert [r["user_id"] for r in await daily_step()] == [me]  # not the Honolulu account


async def test_a_queued_run_waits_for_that_users_own(db: str, sql: psycopg.Connection[Any]) -> None:
    """Inngest lets a queued run's first step start beside a running one; one of my own
    must still wait (T4). Both these runs are the owner's."""
    await _board()
    sql.execute(
        "INSERT INTO run (trigger, mode, status, started_at) "
        "VALUES ('manual', 'find_only', 'running', now())"
    )
    sql.execute("INSERT INTO run (trigger, mode) VALUES ('scheduled', 'find_only')")
    row = sql.execute("SELECT id FROM run WHERE status = 'queued'").fetchone()
    assert row is not None
    async with get_sessionmaker()() as session:
        assert await start_run(session, row[0]) is None
    assert sql.execute("SELECT status FROM run WHERE id = %s", (row[0],)).fetchone() == ("queued",)
    # A run stuck as running for hours (a crash) no longer blocks.
    sql.execute("UPDATE run SET started_at = now() - interval '3 hours' WHERE status = 'running'")
    async with get_sessionmaker()() as session:
        assert await start_run(session, row[0]) is not None
    assert sql.execute("SELECT status FROM run WHERE id = %s", (row[0],)).fetchone() == ("running",)


async def test_another_users_search_does_not_hold_mine_up(
    db: str, sql: psycopg.Connection[Any], other_id: uuid.UUID
) -> None:
    """Searches go side by side, one per user (T6). Until v0.20.0 one queue served the whole
    service and the second person waited for the first, however long they took."""
    mine_board = await _board()
    sql.execute(
        "INSERT INTO run (user_id, trigger, mode, status, started_at)"
        " VALUES (%s, 'manual', 'find_only', 'running', now())",
        (other_id,),
    )
    sql.execute("INSERT INTO run (trigger, mode) VALUES ('manual', 'find_only')")
    row = sql.execute("SELECT id FROM run WHERE user_id <> %s", (other_id,)).fetchone()
    assert row is not None
    with tenant.as_system():
        async with get_sessionmaker()() as session:
            boards = await start_run(session, row[0])
    assert [x["board_id"] for x in boards or []] == [mine_board]  # theirs did not hold it
    assert sql.execute(
        "SELECT status FROM run WHERE id = %s", (row[0],)
    ).fetchone() == ("running",)  # fmt: skip
