import uuid
from typing import Any

import psycopg
import pytest

from database import tenant
from database.models import BoardFollow, Posting, WatchedBoard
from database.session import get_sessionmaker
from functions.fetch_board import close_step, fetch_step
from pipeline.ingest import html_to_text
from schemas.job import Job
from sources import greenhouse
from tests import boards as b

GH = "https://boards-api.greenhouse.io/v1/boards"
SECOND = b.job(b.GREENHOUSE_JOB, id=1002, title="Policy Analyst")


def test_html_to_text() -> None:
    html = "<div><p>Hello&nbsp;there</p><ul><li>One</li><li>Two</li></ul><script>x()</script></div>"
    assert html_to_text(html) == "Hello there\n\n- One\n\n- Two"


async def _board(ats: str = "greenhouse", token: str = "discord") -> str:
    """A board the test's user follows."""
    async with get_sessionmaker()() as session:
        board = WatchedBoard(ats=ats, board_token=token, display_name="Discord", origin="user")
        session.add(board)
        await session.flush()
        session.add(BoardFollow(user_id=tenant.user_id(session), board_id=board.id))
        await session.commit()
        return str(board.id)


def _job() -> Job:
    return greenhouse.to_job(b.GREENHOUSE_JOB, "Discord")


async def test_fetch_stores_postings_and_queues_extraction(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    routes: b.Routes = {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB, SECOND)}
    b.fake_boards(monkeypatch, routes)
    board_id = await _board()

    first = await fetch_step(board_id)
    assert (first["new"], first["changed"], first["unchanged"]) == (2, 0, 0)
    assert len(first["to_enrich"]) == 2
    row = sql.execute(
        "SELECT title_raw, description_html, description_text, raw_payload->>'id', state "
        "FROM posting WHERE external_id = '8806482002'"
    ).fetchone()
    assert row is not None
    title, html, text, raw_id, state = row
    assert title == "Commercial Policy Lead" and state == "open"
    assert html.startswith("<div><p>Discord")  # stored unchanged (I5)
    assert "- Own commercial policy" in text and "<" not in text
    assert raw_id == "8806482002"
    assert sql.execute("SELECT last_ok FROM watched_board").fetchone() == (True,)

    # Mark both extracted: an unchanged re-fetch then queues nothing (I4).
    sql.execute("UPDATE posting SET extracted_hash = description_hash")
    again = await fetch_step(board_id)
    assert (again["new"], again["unchanged"], again["to_enrich"]) == (0, 2, [])
    assert sql.execute("SELECT count(*) FROM posting").fetchone() == (2,)

    # A changed description is queued again.
    routes[f"{GH}/discord/jobs"] = b.greenhouse(
        b.job(b.GREENHOUSE_JOB, content="&lt;p&gt;New text&lt;/p&gt;"), SECOND
    )
    changed = await fetch_step(board_id)
    assert changed["changed"] == 1 and len(changed["to_enrich"]) == 1


async def test_missing_postings_are_closed_not_deleted(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    routes: b.Routes = {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB, SECOND)}
    b.fake_boards(monkeypatch, routes)
    board_id = await _board()
    await fetch_step(board_id)

    routes[f"{GH}/discord/jobs"] = b.greenhouse(b.GREENHOUSE_JOB)
    fetched = await fetch_step(board_id)
    assert await close_step(board_id, fetched["seen"]) == 1
    assert sql.execute(
        "SELECT external_id, state, closed_at IS NOT NULL FROM posting ORDER BY external_id"
    ).fetchall() == [("1002", "closed", True), ("8806482002", "open", False)]

    # Back on the board: reopened, same row.
    routes[f"{GH}/discord/jobs"] = b.greenhouse(b.GREENHOUSE_JOB, SECOND)
    back = await fetch_step(board_id)
    assert back["reopened"] == 1
    assert sql.execute("SELECT count(*) FROM posting WHERE state = 'open'").fetchone() == (2,)


# The board freshness window (T6) -------------------------------------------------------


@pytest.fixture
def fresh_window(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The window on for one test. conftest.py turns it off for the rest of the suite.
    Takes db, which clears the settings cache, so this patch is the last word."""
    from config import get_settings

    monkeypatch.setattr(get_settings(), "jap_board_fresh_minutes", 10)


async def _fetch(board_id: str) -> dict[str, Any]:
    """fetch_step as fetch-board really runs it: as the service, because a board is shared
    and who wants a title is read across every follower (pipeline/enrich.py: wanted_on)."""
    with tenant.as_system():
        return await fetch_step(board_id)


async def test_a_board_someone_just_fetched_is_not_fetched_again(
    fresh_window: None, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A board is shared, so two people searching minutes apart share one fetch (T6).
    The second search asks the ATS nothing and closes nothing."""
    calls: list[str] = []
    routes: b.Routes = {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB, SECOND)}
    b.fake_boards(monkeypatch, routes, seen=calls)
    board_id = await _board()

    first = await _fetch(board_id)
    assert first["new"] == 2 and not first.get("fresh") and len(calls) == 1

    again = await _fetch(board_id)
    assert again["fresh"] is True
    assert len(calls) == 1  # the job board was not asked a second time
    assert (again["fetched"], again["new"], again["seen"]) == (0, 0, [])
    assert sql.execute("SELECT count(*) FROM posting WHERE state = 'open'").fetchone() == (2,)

    # Outside the window it is fetched again, as before.
    sql.execute("UPDATE watched_board SET last_checked_at = now() - interval '11 minutes'")
    later = await _fetch(board_id)
    assert not later.get("fresh") and later["unchanged"] == 2 and len(calls) == 2


async def test_a_fresh_skip_still_reads_what_somebody_now_wants(
    fresh_window: None, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    other_id: uuid.UUID,
) -> None:  # fmt: skip
    """The subtle half. Who wants a title is worked out at fetch time, so a user who
    follows the company after that fetch was not in the set. Skipping the HTTP call is
    right; skipping their reading would leave their jobs never read."""
    routes: b.Routes = {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB, SECOND)}
    b.fake_boards(monkeypatch, routes)
    board_id = await _board()
    # The owner wants only one of the two titles, so only that one is read.
    sql.execute("INSERT INTO profile (target_roles) VALUES ('{Policy Analyst}')")
    first = await _fetch(board_id)
    assert len(first["to_enrich"]) == 1
    sql.execute("UPDATE posting SET extracted_hash = description_hash WHERE title_raw = %s",
                ("Policy Analyst",))  # fmt: skip

    # Somebody else follows the same company, wanting the other title. Their search is
    # inside the window, so nothing is fetched — but their job is still queued to read.
    sql.execute(
        "INSERT INTO profile (user_id, target_roles) VALUES (%s, '{Commercial Policy Lead}')",
        (other_id,),
    )
    sql.execute(
        "INSERT INTO board_follow (user_id, board_id) VALUES (%s, %s)", (other_id, board_id)
    )
    theirs = await _fetch(board_id)
    assert theirs["fresh"] is True and theirs["fetched"] == 0
    titles = sql.execute(
        "SELECT title_raw FROM posting WHERE id = ANY(%s::uuid[])", (theirs["to_enrich"],)
    ).fetchall()
    assert titles == [("Commercial Policy Lead",)]


async def test_a_first_fetch_and_a_failed_one_are_never_fresh(
    fresh_window: None, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Following a company and searching at once must not show an empty list, and a board
    whose last fetch failed is tried again rather than trusted."""
    b.fake_boards(monkeypatch, {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB)})
    board_id = await _board()
    assert not (await _fetch(board_id)).get("fresh")  # never checked before
    sql.execute("UPDATE watched_board SET last_ok = false")
    assert not (await _fetch(board_id)).get("fresh")  # the last one failed


async def test_board_not_found_is_marked_and_does_not_raise(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    b.fake_boards(monkeypatch, {f"{GH}/discord/jobs": 404})
    board_id = await _board()
    result = await fetch_step(board_id)
    assert result["ok"] is False and "not found" in result["error"]
    assert sql.execute("SELECT last_ok FROM watched_board").fetchone() == (False,)


async def test_posting_maps_back_to_job(
    db: str, owner: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    b.fake_boards(monkeypatch, {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB)})
    await fetch_step(await _board())
    async with get_sessionmaker()() as session:
        from sqlalchemy import select

        p = await session.scalar(select(Posting))
        assert p is not None
        job = Job(
            source=p.source, external_id=p.external_id, company_name=p.company_name_raw,
            title=p.title_raw, locations=p.locations, department=p.department,
            employment_type=p.employment_type, posted_at=p.posted_at,
            posting_url=p.posting_url, apply_url=p.apply_url,
            requisition_id=p.requisition_id, description_html=p.description_html,
            raw_payload=p.raw_payload,
        )  # fmt: skip
    assert job.model_dump(exclude={"posted_at"}) == _job().model_dump(exclude={"posted_at"})
    assert uuid.UUID(str(p.id))
