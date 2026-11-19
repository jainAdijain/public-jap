"""Each user reads and changes only their own data (PRD S7), checked three ways: through
every route with another user's ids, through one shared fetch that gives two users two
lists, and in Postgres itself, where row-level security refuses what the code forgot."""

import json
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from sqlalchemy import select, text

import llm.client as client
from database import tenant
from database.models import Application, LlmCall, Profile, Run, UserListing
from database.session import get_sessionmaker
from functions.enrich_posting import extract_step
from functions.fetch_board import close_step, fetch_step
from functions.run_pipeline import filter_step
from llm import SpendCapReached, call
from llm.redact import Sensitive
from pipeline.apply import flow
from pipeline.dedup import dedup
from schemas.resume import ParsedResume
from tests import boards as b
from tests import mine
from tests.conftest import _libpq
from tests.fixtures import RESUME, pdf_bytes
from tests.test_enrich import FULL
from tests.test_llm_call import GOOD, Stub

GH = "https://boards-api.greenhouse.io/v1/boards"
DISCORD = {"ats": "greenhouse", "board_token": "discord", "display_name": "Discord"}
BACKEND = b.job(b.GREENHOUSE_JOB, id=3001, title="Backend Engineer", requisition_id="R-1",
                location={"name": "Bangalore"})  # fmt: skip
ANALYST = b.job(b.GREENHOUSE_JOB, id=3002, title="Policy Analyst", requisition_id="R-2",
                location={"name": "Bangalore"})  # fmt: skip
NOBODY = "00000000-0000-0000-0000-000000000000"


# --- the database repeats the check (0013_rls) --------------------------------------------------


def test_every_private_table_has_a_user_and_a_policy(sql: psycopg.Connection[Any]) -> None:
    """Static: any table with a user_id column is in PRIVATE_TABLES (or is one of the auth and
    log tables that are not a workspace), and every one of those has row-level security on
    with a policy for the app's role that tests user_id against app.user_id."""
    with_user = {
        r[0]
        for r in sql.execute(
            "SELECT table_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND column_name = 'user_id'"
        ).fetchall()
    }
    not_workspace = {"auth_session", "password_reset", "email_verification", "log_event",
                     "alert_state"}  # fmt: skip
    assert with_user - not_workspace == set(tenant.PRIVATE_TABLES)
    secured = {
        r[0]
        for r in sql.execute(
            "SELECT relname FROM pg_class"
            " WHERE relrowsecurity AND relnamespace = 'public'::regnamespace"
        ).fetchall()
    }
    assert secured == set(tenant.PRIVATE_TABLES)
    policies = sql.execute(
        "SELECT tablename, roles::text[], qual, with_check FROM pg_policies"
        " WHERE schemaname = 'public'"
    ).fetchall()
    assert {p[0] for p in policies} == set(tenant.PRIVATE_TABLES)
    for table, roles, using, check in policies:
        assert roles == [tenant.APP_ROLE], table
        assert "user_id" in using and "app.user_id" in using and using == check, table
    owner_is_not_the_app = sql.execute(
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s", (tenant.APP_ROLE,)
    ).fetchone()
    assert owner_is_not_the_app == (False,)


async def test_no_user_in_scope_means_no_rows(
    api: httpx.AsyncClient, other: httpx.AsyncClient, other_id: uuid.UUID, owner: uuid.UUID
) -> None:
    """A query on a private table that forgot its filter: with nobody in scope it returns
    nothing, and as one user it returns only theirs. Only the service sees every row."""
    await api.put("/api/profile", json={"full_name": "Asha Rao"})
    await other.put("/api/profile", json={"full_name": "Other Person"})
    unfiltered = select(Profile.full_name).order_by(Profile.full_name)

    async def names(scope: tenant.Tenant) -> list[str | None]:
        async with get_sessionmaker()() as session:
            session.info[tenant.INFO_KEY] = scope
            return list(await session.scalars(unfiltered))

    assert await names(tenant.NOBODY) == []
    assert await names(tenant.user(owner)) == ["Asha Rao"]
    assert await names(tenant.user(other_id)) == ["Other Person"]
    assert await names(tenant.SYSTEM) == ["Asha Rao", "Other Person"]

    async with get_sessionmaker()() as session:  # and it cannot write as someone else
        session.info[tenant.INFO_KEY] = tenant.user(other_id)
        changed = await session.execute(text("UPDATE profile SET full_name = 'Hacked'"))
        assert getattr(changed, "rowcount", None) == 1  # only their own row
        await session.rollback()
        with pytest.raises(Exception, match="row-level security"):
            await session.execute(
                text("INSERT INTO run (user_id, trigger, mode) VALUES (:u, 'manual', 'find_only')"),
                {"u": owner},
            )


async def test_private_code_without_a_user_fails_loudly(db: str) -> None:
    with tenant.as_system():
        async with get_sessionmaker()() as session:
            with pytest.raises(tenant.NoTenant):
                await flow.applied_today(session, __import__("datetime").datetime.now())


# --- one user's ids through every route ---------------------------------------------------------


async def _owner_data(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], tmp_path: Path
) -> dict[str, str]:
    """A bit of everything that is the owner's: a resume, an answer, a company, a listed
    job, a run, and an application with a screenshot and a cover letter on disk."""
    from tests.test_apply import _listing

    resume = (
        await api.post(
            "/api/resumes",
            files={"file": ("resume.pdf", pdf_bytes(RESUME), "application/octet-stream")},
        )
    ).json()
    answer = (
        await api.post("/api/answers", json={"question": "Notice period?", "answer": "30 days"})
    ).json()
    run = (await api.post("/api/runs", json={})).json()
    listing = _listing(sql)
    board = sql.execute("SELECT id FROM watched_board").fetchone()
    posting = sql.execute("SELECT id FROM posting").fetchone()
    assert board is not None and posting is not None
    shot = tmp_path / "users" / "x" / "shot.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG owner's screenshot")
    letter = tmp_path / "users" / "x" / "letter.pdf"
    letter.write_bytes(b"%PDF owner's letter")
    app = sql.execute(
        "INSERT INTO application (listing_id, posting_id, status, mode, resume_version_id,"
        " screenshot_path, cover_letter_path, cover_letter_text, answers, form_fields)"
        " VALUES (%s, %s, 'ready_for_review', 'review', %s, %s, %s, 'Dear Acme', %s, '[]')"
        " RETURNING id",
        (listing, posting[0], resume["id"], str(shot), str(letter),
         json.dumps({"salary": "a private number"})),
    ).fetchone()  # fmt: skip
    assert app is not None
    mine.status(sql, "ready_for_review", "id = %s", (listing,))
    return {
        "resume": resume["id"], "answer": answer["id"], "run": run["id"], "listing": listing,
        "board": str(board[0]), "posting": str(posting[0]), "application": str(app[0]),
    }  # fmt: skip


async def test_another_users_ids_answer_not_found_on_every_route(
    api: httpx.AsyncClient, other: httpx.AsyncClient, sql: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:  # fmt: skip
    """User B, properly signed in, asks for each of user A's things by id, on every route
    that takes one. Every answer is Not Found (or a refusal), never A's data, and nothing of
    A's changes. Every route with an id in its path is covered: a new one fails this test
    until it is added here."""
    ids = await _owner_data(api, sql, tmp_path)
    a = ids["application"]
    body: dict[str, Any] = {"answers": {}, "save_to_bank": False}
    tries: list[tuple[str, str, Any]] = [
        ("GET", f"/api/resumes/{ids['resume']}/file", None),
        (
            "POST",
            f"/api/resumes/{ids['resume']}/accept",
            {"must_have_skills": [], "nice_to_have_skills": []},
        ),
        ("PUT", f"/api/answers/{ids['answer']}", {"question": "q", "answer": "theirs now"}),
        ("DELETE", f"/api/answers/{ids['answer']}", None),
        ("GET", f"/api/runs/{ids['run']}", None),
        ("GET", f"/api/boards/{ids['board']}/unfollow", None),
        ("DELETE", f"/api/boards/{ids['board']}", None),
        ("GET", f"/api/postings/{ids['posting']}", None),
        ("POST", f"/api/postings/{ids['posting']}/split", None),
        ("GET", f"/api/listings/{ids['listing']}", None),
        ("POST", f"/api/listings/{ids['listing']}/apply", None),
        ("POST", f"/api/listings/{ids['listing']}/skip", None),
        ("POST", f"/api/listings/{ids['listing']}/unskip", None),
        ("POST", f"/api/listings/{ids['listing']}/merge", {"other_listing_id": NOBODY}),
        ("GET", f"/api/applications/{a}", None),
        ("GET", f"/api/applications/{a}/timeline", None),
        ("PUT", f"/api/applications/{a}", {"answers": {}}),
        ("POST", f"/api/applications/{a}/approve", None),
        ("POST", f"/api/applications/{a}/answer", body),
        ("POST", f"/api/applications/{a}/retry", None),
        ("GET", f"/api/files/screenshot/{a}", None),
        ("GET", f"/api/files/trace/{a}", None),
        ("GET", f"/api/files/cover_letter/{a}", None),
        ("POST", f"/api/duplicates/{NOBODY}", {"decision": "merged"}),
        ("GET", f"/api/ops/form-check/{NOBODY}/screenshot", None),
        ("POST", f"/api/admin/accounts/{NOBODY}/off", None),
        ("POST", f"/api/admin/accounts/{NOBODY}/on", None),
        ("PUT", f"/api/admin/accounts/{NOBODY}/limits", {"ai_usd": 100}),
    ]
    from main import app

    with_id = {
        (method.upper(), re.sub(r"\{[^}]+\}", "{}", path))
        for path, methods in app.openapi()["paths"].items()
        if "{" in path
        for method in methods
    }
    known = set(ids.values()) | {NOBODY}

    def shape(path: str) -> str:
        parts = path.split("/")
        kind = parts.index("files") + 1 if "files" in parts else -1  # /api/files/{kind}/{id}
        return "/".join("{}" if x in known or i == kind else x for i, x in enumerate(parts))

    covered = {(m, shape(path)) for m, path, _ in tries}
    assert with_id == covered

    for method, path, payload in tries:
        resp = await other.request(method, path, json=payload)
        assert resp.status_code == 404, f"{method} {path} answered {resp.status_code}"
        assert "private number" not in resp.text and "Dear Acme" not in resp.text

    # The owner still has all of it, unchanged, and can reach every file.
    assert (await api.get(f"/api/applications/{a}")).json()["status"] == "ready_for_review"
    assert (await api.get("/api/answers")).json()[0]["answer"] == "30 days"
    assert (await api.get(f"/api/files/screenshot/{a}")).content.startswith(b"\x89PNG")
    assert (await api.get(f"/api/files/cover_letter/{a}")).content.startswith(b"%PDF")
    assert (await api.get(f"/api/resumes/{ids['resume']}/file")).status_code == 200
    assert (await api.get(f"/api/listings/{ids['listing']}")).json()["status"] == "ready_for_review"
    assert [x["id"] for x in (await api.get("/api/boards")).json()] == [ids["board"]]


async def test_lists_hold_only_my_own(
    api: httpx.AsyncClient, other: httpx.AsyncClient, sql: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:  # fmt: skip
    """The bigger hole before S7: no id needed at all. Every list route, as user B."""
    await api.put("/api/profile", json={"full_name": "Asha Rao", "phone": "+91 98765 43210"})
    await _owner_data(api, sql, tmp_path)
    for path in ("/api/resumes", "/api/answers", "/api/boards", "/api/runs"):
        assert (await other.get(path)).json() == [], path
        assert len((await api.get(path)).json()) == 1, path
    for path in ("/api/listings", "/api/postings"):
        assert (await other.get(path)).json()["total"] == 0, path
        assert (await api.get(path)).json()["total"] == 1, path
    theirs = (await other.get("/api/profile")).json()
    assert theirs["full_name"] is None and theirs["phone"] is None
    state = (await other.get("/api/runs/state")).json()
    assert state == {"last": None, "active": None, "queued": None}
    assert (await other.get("/api/runs/latest")).json() is None
    summary = (await other.get("/api/listings/summary")).json()
    assert summary["totals"]["listings"] == 0 and summary["locations"] == []
    insights = (await other.get("/api/insights")).json()
    assert insights["totals"]["found"] == 0 and insights["totals"]["prepared"] == 0
    assert (await api.get("/api/insights")).json()["totals"]["prepared"] == 1


async def test_files_are_kept_per_user(
    api: httpx.AsyncClient, other: httpx.AsyncClient, owner: uuid.UUID, other_id: uuid.UUID,
    sql: psycopg.Connection[Any], tmp_path: Path,
) -> None:  # fmt: skip
    for who in (api, other):
        up = await who.post(
            "/api/resumes",
            files={"file": ("resume.pdf", pdf_bytes(RESUME), "application/octet-stream")},
        )
        assert up.status_code == 201
    paths = dict(sql.execute("SELECT user_id, file_path FROM resume_version").fetchall())
    for uid in (owner, other_id):
        assert Path(paths[uid]).is_relative_to(tmp_path / "users" / str(uid) / "resumes")
    # Each has one active resume: the second upload did not switch the first user's off.
    assert sql.execute("SELECT count(*) FROM resume_version WHERE is_active").fetchone() == (2,)
    # A row that points outside data/ is never served, whoever owns it.
    sql.execute("UPDATE resume_version SET file_path = '/etc/hosts' WHERE user_id = %s", (owner,))
    mine_id = (await api.get("/api/resumes")).json()[0]["id"]
    assert (await api.get(f"/api/resumes/{mine_id}/file")).status_code == 404


# --- shared jobs, private lists -----------------------------------------------------------------


async def _both_follow(
    api: httpx.AsyncClient, other: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, Stub]:
    """Two users with different profiles follow one company; one search runs for it."""
    b.fake_boards(monkeypatch, {f"{GH}/discord/jobs": b.greenhouse(BACKEND, ANALYST)})
    place = {"locations": ["Bangalore"]}
    await api.put("/api/profile", json={"target_roles": ["Backend Engineer"], **place})
    await other.put("/api/profile", json={"target_roles": ["Policy Analyst"], **place})
    first = await api.post("/api/boards", json=DISCORD)
    second = await other.post("/api/boards", json={**DISCORD, "display_name": "Discord Inc"})
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]  # one board, two follows
    stub = Stub([json.dumps(FULL), json.dumps(FULL)])
    monkeypatch.setattr(client, "chat", stub)
    return str(first.json()["id"]), stub


async def _search(board_id: str, run_id: str) -> dict[str, Any]:
    """One search as the service runs it: fetch, read, dedup, filter."""
    with tenant.as_system():
        fetched = await fetch_step(board_id)
        await close_step(board_id, fetched["seen"])
        for posting_id in fetched["to_enrich"]:
            await extract_step(posting_id)
        async with get_sessionmaker()() as session:
            await dedup(session)
        await filter_step(run_id)
    return fetched


async def _titles(who: httpx.AsyncClient, status: str = "open") -> list[str]:
    page = (await who.get("/api/listings", params={"status": status})).json()
    return sorted(r["title"] for r in page["rows"])


async def test_one_fetch_two_lists(
    api: httpx.AsyncClient, other: httpx.AsyncClient, sql: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    board_id, stub = await _both_follow(api, other, monkeypatch)
    run = (await api.post("/api/runs", json={})).json()  # only the owner searches
    fetched = await _search(board_id, run["id"])

    assert fetched["new"] == 2 and len(fetched["to_enrich"]) == 2
    assert sql.execute("SELECT count(*) FROM watched_board").fetchone() == (1,)
    assert sql.execute("SELECT count(*) FROM posting").fetchone() == (2,)  # stored once
    assert sql.execute("SELECT count(*) FROM listing").fetchone() == (2,)
    assert len(stub.bodies) == 2  # each description read by the AI once, for both of them
    calls = sql.execute("SELECT user_id, billed_to FROM llm_call").fetchall()
    assert calls == [(None, "system"), (None, "system")]  # nobody's allowance
    for body in stub.bodies:  # and nothing of either profile went with it
        assert "Bangalore" not in json.dumps(body["messages"][0])

    # The other user did not search, and their list is there all the same.
    assert await _titles(api) == ["Backend Engineer"]
    assert await _titles(other) == ["Policy Analyst"]
    assert sql.execute("SELECT count(*) FROM user_listing").fetchone() == (2,)
    for who in (api, other):
        assert (await who.get("/api/postings")).json()["total"] == 2  # the board is theirs

    # A second search reads nothing again and changes neither list.
    again = await _search(board_id, run["id"])
    assert again["to_enrich"] == [] and len(stub.bodies) == 2
    assert await _titles(api) == ["Backend Engineer"]
    assert await _titles(other) == ["Policy Analyst"]


async def test_a_job_nobody_wants_is_not_read(
    api: httpx.AsyncClient, other: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The service's AI budget goes on jobs someone could be shown: a title in no follower's
    target roles is stored and embedded, not sent to the model."""
    board_id, stub = await _both_follow(api, other, monkeypatch)
    await other.put("/api/profile", json={"target_roles": ["Data Scientist"]})
    run = (await api.post("/api/runs", json={})).json()
    fetched = await _search(board_id, run["id"])
    assert fetched["new"] == 2 and len(fetched["to_enrich"]) == 1 and len(stub.bodies) == 1
    assert (await api.get("/api/runs/state")).json()["queued"]["extracting"] == 0
    # Once someone wants it, the next search reads it.
    await other.put("/api/profile", json={"target_roles": ["Policy Analyst"]})
    assert len((await _search(board_id, run["id"]))["to_enrich"]) == 1 and len(stub.bodies) == 2


async def test_my_unfollow_hide_and_apply_leave_your_list_alone(
    api: httpx.AsyncClient, other: httpx.AsyncClient, sql: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:  # fmt: skip
    board_id, _ = await _both_follow(api, other, monkeypatch)
    both = {"target_roles": ["Backend Engineer", "Policy Analyst"], "locations": ["Bangalore"]}
    await api.put("/api/profile", json=both)
    await other.put("/api/profile", json=both)
    run = (await api.post("/api/runs", json={})).json()
    await _search(board_id, run["id"])
    everything = ["Backend Engineer", "Policy Analyst"]
    assert await _titles(api) == await _titles(other) == everything
    ids = {r["title"]: r["id"] for r in (await api.get("/api/listings")).json()["rows"]}

    # Hide: mine only.
    hidden = await api.post(f"/api/listings/{ids['Policy Analyst']}/skip")
    assert hidden.json()["status"] == "skipped"
    assert await _titles(api, "active") == ["Backend Engineer"]
    assert await _titles(other, "active") == everything

    # Apply: my application, my status. The other user can still apply to the same job.
    from tests.test_apply import _resume

    _resume(sql, tmp_path)
    mine.opt_in(sql, automatic=False)  # JAP may apply in my name (S14)
    async with get_sessionmaker()() as session:
        app_id = await flow.create_attempt(session, ids["Backend Engineer"], None)
    backend = f"/api/listings/{ids['Backend Engineer']}"
    assert (await api.get(backend)).json()["status"] == "applying"
    theirs = (await other.get(backend)).json()
    assert theirs["status"] == "not_applied" and theirs["application"] is None
    assert theirs["attempts"] == 0
    assert (await other.get(f"/api/applications/{app_id}")).status_code == 404

    # Unfollow: the jobs close for me; the board stays on and open for the other follower.
    assert (await api.delete(f"/api/boards/{board_id}")).status_code == 204
    assert await _titles(api) == ["Backend Engineer"]  # the one with an application stays (I12)
    assert (await api.get("/api/boards")).json() == []
    assert await _titles(other) == everything
    assert [x["id"] for x in (await other.get("/api/boards")).json()] == [board_id]
    assert sql.execute("SELECT active FROM watched_board").fetchone() == (True,)
    assert sql.execute("SELECT count(*) FROM posting WHERE state = 'open'").fetchone() == (2,)

    # The last follower leaving switches the board off and closes its postings, as before.
    assert (await other.delete(f"/api/boards/{board_id}")).status_code == 204
    assert sql.execute("SELECT active FROM watched_board").fetchone() == (False,)
    assert sql.execute(
        "SELECT count(*) FROM posting WHERE state = 'closed' AND close_reason = 'unfollowed'"
    ).fetchone() == (2,)
    assert await _titles(other) == []


async def test_the_free_plan_caps_companies_followed(
    api: httpx.AsyncClient, other: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import get_settings

    monkeypatch.setattr(get_settings(), "jap_free_boards", 2)
    for who, expected in ((other, [201, 201, 409]), (api, [201, 201, 201])):  # not the admin
        codes = []
        for token in ("one", "two", "three"):
            board = {"ats": "lever", "board_token": token, "display_name": token.title()}
            codes.append((await who.post("/api/boards", json=board)).status_code)
        assert codes == expected
    refused = await other.post(
        "/api/boards", json={"ats": "lever", "board_token": "four", "display_name": "Four"}
    )
    assert refused.json()["error"]["code"] == "board_limit"


# --- each user's own AI allowance (S12) ---------------------------------------------------------


async def test_the_admin_sets_one_accounts_limits(
    api: httpx.AsyncClient, other: httpx.AsyncClient, other_id: uuid.UUID
) -> None:
    """The free plan is 1 company, 1 application a day and $1 of AI a month. The admin can
    give any one account its own numbers, and put it back on the plan."""
    plan = (await other.get("/api/profile")).json()
    free = {"name": "Free", "boards": 1, "applications_per_day": 1, "ai_usd": 1.0, "custom": False}
    assert plan["plan"] == free and plan["daily_cap"] == 1 and plan["spend"]["cap_usd"] == 1.0
    board = {"ats": "lever", "board_token": "one", "display_name": "One"}
    assert (await other.post("/api/boards", json=board)).status_code == 201
    second = await other.post("/api/boards", json={**board, "board_token": "two"})
    assert second.status_code == 409
    assert "Your plan follows 1 company." in second.json()["error"]["message"]
    # A user cannot raise their own daily cap past the plan, whatever they type.
    typed = (await other.patch("/api/profile", json={"daily_cap": 50})).json()
    assert typed["daily_cap"] == 1

    limits = {"ai_usd": 7.5, "boards": 3, "applications_per_day": 10}
    path = f"/api/admin/accounts/{other_id}/limits"
    assert (await other.put(path, json=limits)).status_code == 404  # only the admin
    saved = await api.put(path, json=limits)
    assert saved.status_code == 200
    account = saved.json()["accounts"][1]
    assert account["plan"] == {"name": "Free", "boards": 3, "applications_per_day": 10,
                               "ai_usd": 7.5, "custom": True}  # fmt: skip
    mine_now = (await other.get("/api/profile")).json()
    assert mine_now["plan"]["custom"] is True and mine_now["spend"]["cap_usd"] == 7.5
    assert mine_now["daily_cap"] == 10  # the 50 they typed, held to the new limit
    assert (
        await other.post("/api/boards", json={**board, "board_token": "two"})
    ).status_code == 201

    # One number alone, and back to the plan: a number left out is the plan's again.
    only_ai = (await api.put(path, json={"ai_usd": 3})).json()["accounts"][1]["plan"]
    assert (only_ai["ai_usd"], only_ai["boards"], only_ai["applications_per_day"]) == (3.0, 1, 1)
    back = (await api.put(path, json={})).json()["accounts"][1]["plan"]
    assert back == free
    assert len((await other.get("/api/boards")).json()) == 2  # lowering unfollows nothing
    third = await other.post("/api/boards", json={**board, "board_token": "three"})
    assert third.status_code == 409
    assert (await api.put(path, json={"applications_per_day": 500})).status_code == 422


async def _parse(user_id: uuid.UUID | None) -> None:
    scope = tenant.as_user(user_id) if user_id else tenant.as_system()
    with scope:
        async with get_sessionmaker()() as session:
            await call(
                session, "resume_parse", {"resume_text": "Python"}, ParsedResume,
                sensitive=Sensitive(),
            )  # fmt: skip


async def test_one_user_at_their_limit_stops_only_them(
    api: httpx.AsyncClient, other: httpx.AsyncClient, owner: uuid.UUID, other_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    monkeypatch.setattr(client, "chat", Stub([GOOD] * 6, cost=2.5))
    await _parse(other_id)  # $2.50 against a $1 allowance
    with pytest.raises(SpendCapReached, match=r"\$2.50 of \$1.00"):
        await _parse(other_id)
    await _parse(owner)  # the owner carries on
    await _parse(None)  # and so does reading shared postings
    assert (await other.get("/api/profile")).json()["spend"] == {
        "month_usd": 2.5, "cap_usd": 1.0, "reached": True,
    }  # fmt: skip
    assert (await api.get("/api/profile")).json()["spend"]["month_usd"] == 2.5
    async with get_sessionmaker()() as session:
        session.info[tenant.INFO_KEY] = tenant.SYSTEM
        rows = (await session.execute(select(LlmCall.user_id, LlmCall.billed_to))).all()
    assert sorted(rows, key=str) == sorted(
        [(other_id, "user"), (other_id, "user"), (owner, "user"), (None, "system")], key=str
    )

    # A user can lower their own limit, not raise it above the plan; the admin's stands.
    raised = (await other.patch("/api/profile", json={"spend_cap_usd": 50})).json()
    assert raised["spend"]["cap_usd"] == 1.0 and raised["spend"]["reached"] is True
    lowered = (await other.patch("/api/profile", json={"spend_cap_usd": 0.5})).json()
    assert lowered["spend"]["cap_usd"] == 0.5
    admins = (await api.patch("/api/profile", json={"spend_cap_usd": 50})).json()
    assert admins["spend"]["cap_usd"] == 50.0 and admins["spend"]["reached"] is False


async def test_the_service_budget_and_the_total_limit(
    db: str, owner: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import get_settings

    monkeypatch.setattr(client, "chat", Stub([GOOD] * 6, cost=3.0))
    monkeypatch.setattr(get_settings(), "jap_system_cost_cap_usd", 5.0)
    monkeypatch.setattr(get_settings(), "jap_monthly_cost_cap_usd", 100.0)
    await _parse(None)
    await _parse(None)  # $6 against the service's $5
    with pytest.raises(SpendCapReached):
        await _parse(None)
    await _parse(owner)  # users are not stopped by the service's budget
    monkeypatch.setattr(get_settings(), "jap_total_cost_cap_usd", 9.0)
    with pytest.raises(SpendCapReached, match=r"\$9.00 of \$9.00"):  # everything together
        await _parse(owner)


async def test_background_work_is_the_events_user(
    api: httpx.AsyncClient, other: httpx.AsyncClient, other_id: uuid.UUID,
    sql: psycopg.Connection[Any], events: list[tuple[str, dict[str, Any]]],
) -> None:  # fmt: skip
    """S13: an event carries its user, and the function runs as them and as nobody else."""
    from observability.inngest_mw import event_user

    await other.post("/api/runs", json={})
    await other.put("/api/profile", json={"target_roles": ["Engineer"]})
    assert {name for name, _ in events} == {"run/requested", "profile/saved"}
    assert all(data["user_id"] == str(other_id) for _, data in events)
    assert event_user({"run_id": "r", "user_id": str(other_id)}) == str(other_id)
    failed = {"event": {"data": {"user_id": str(other_id)}}}  # what an on_failure handler gets
    assert event_user(failed) == str(other_id)
    assert event_user({"board_id": "b"}) is None  # shared work: the service's

    # As the owner (this test's tenant), the other user's run and rows are not there.
    run_id = sql.execute("SELECT id FROM run").fetchone()
    assert run_id is not None
    async with get_sessionmaker()() as session:
        assert await flow.pick(session, str(run_id[0])) == []
        assert list(await session.scalars(select(Run))) == []
        assert list(await session.scalars(select(Application))) == []
        assert list(await session.scalars(select(UserListing))) == []


def test_owner_connection_is_what_the_tests_assume(db: str, owner: uuid.UUID) -> None:
    """`sql` and the service see every row because they are the table owner, not the app."""
    with psycopg.connect(_libpq(db), autocommit=True) as conn:
        assert conn.execute("SELECT current_user <> %s", (tenant.APP_ROLE,)).fetchone() == (True,)
