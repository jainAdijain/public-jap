"""Export my data, and delete my account (PRD S15).

The deletion is the dangerous half, so most of this is about what it must *not* touch: the
other account's rows, the shared jobs and companies, and the frozen-application rule, which
opens for erasure and for nothing else.

Rows belonging to the second account are written with their user_id named, because the
`sql` connection's app.user_id is the owner's and that is what every user_id column
defaults to (tests/conftest.py).
"""

import json
import uuid
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from sqlalchemy import text

from auth.consent import TERMS_VERSION
from database import tenant
from database.session import get_sessionmaker
from pipeline import erasure, storage
from tests.conftest import (
    OTHER_EMAIL,
    OTHER_PASSWORD,
    OWNER_EMAIL,
    OWNER_PASSWORD,
    _client,
    _sign_in,
)
from tests.test_apply import _listing

DELETE = {"password": OTHER_PASSWORD, "confirm_email": OTHER_EMAIL}


def _accounts(sql: psycopg.Connection[Any], user_id: uuid.UUID) -> tuple[Any, ...] | None:
    return sql.execute("SELECT count(*) FROM app_user WHERE id = %s", (user_id,)).fetchone()


def test_every_private_table_is_exported_and_deleted() -> None:
    """The one that keeps this honest. A private table added later and forgotten here
    fails this test instead of quietly outliving the account it belongs to."""
    assert set(erasure.FILES) == set(tenant.PRIVATE_TABLES)
    assert set(erasure.DELETE_ORDER) | set(erasure.ANONYMISED) == set(tenant.PRIVATE_TABLES)
    # A table is either deleted or kept with the person taken out of it, never both.
    assert not set(erasure.DELETE_ORDER) & set(erasure.ANONYMISED)


async def _belongings(
    other: httpx.AsyncClient, sql: psycopg.Connection[Any], other_id: uuid.UUID, data: Path
) -> dict[str, Any]:
    """The second account with something to lose: a profile, a resume on disk, a saved
    answer, a followed company, a matched job and an Applied application with a screenshot.
    The company, board, listing and posting are shared, and the owner follows them too."""
    await other.put(
        "/api/profile", json={"full_name": "Other Person", "target_roles": ["Backend Engineer"]}
    )
    await other.post("/api/answers", json={"question": "Notice period?", "answer": "30 days"})
    listing = _listing(sql)  # shared, and the owner follows the board it is on
    board = sql.execute("SELECT id FROM watched_board").fetchone()
    assert board is not None
    sql.execute(
        "INSERT INTO board_follow (user_id, board_id) VALUES (%s, %s)", (other_id, board[0])
    )
    sql.execute(
        "INSERT INTO user_listing (user_id, listing_id, matched) VALUES (%s, %s, '{role}')",
        (other_id, listing),
    )
    path = data / "their-resume.pdf"
    path.write_bytes(b"%PDF-1.4 resume")
    resume = sql.execute(
        "INSERT INTO resume_version (user_id, file_path, file_sha256, original_name, mime,"
        " is_active) VALUES (%s, %s, repeat('0', 64), 'Other.pdf', 'application/pdf', true)"
        " RETURNING id",
        (other_id, str(path)),
    ).fetchone()
    assert resume is not None
    shot = storage.user_path(other_id, "screenshots", "kept.png")
    shot.write_bytes(b"png")
    sql.execute(
        "INSERT INTO application (user_id, listing_id, posting_id, resume_version_id, mode,"
        " status, screenshot_path) SELECT %s, %s, primary_posting_id, %s, 'review', 'applied', %s"
        " FROM listing WHERE id = %s",
        (other_id, listing, resume[0], str(shot), listing),
    )
    sql.execute(
        "INSERT INTO llm_call (purpose, model, prompt_version, input_sha256, cost_usd, user_id,"
        " billed_to) VALUES ('answer', 'm', 'v1', repeat('a', 64), 0.02, %s, 'user')",
        (other_id,),
    )
    return {"listing": listing, "resume": resume[0], "shot": shot, "board": board[0]}


async def test_delete_takes_everything_of_mine_and_nothing_of_yours(
    api: httpx.AsyncClient, other: httpx.AsyncClient, other_id: uuid.UUID,
    sql: psycopg.Connection[Any], tmp_path: Path,
) -> None:  # fmt: skip
    kept = await _belongings(other, sql, other_id, tmp_path)
    owner_rows = {
        t: sql.execute(f"SELECT count(*) FROM {t} WHERE user_id <> %s", (other_id,)).fetchone()
        for t in tenant.PRIVATE_TABLES
    }

    gone = await other.post("/api/account/delete", json=DELETE)
    assert gone.status_code == 200, gone.text
    assert gone.json() == {"deleted": True}

    # Nothing of theirs is left anywhere, and llm_call kept the cost without the person.
    for table in erasure.DELETE_ORDER:
        left = sql.execute(
            f"SELECT count(*) FROM {table} WHERE user_id = %s", (other_id,)
        ).fetchone()
        assert left == (0,), table
    assert _accounts(sql, other_id) == (0,)
    spend = sql.execute("SELECT user_id, billed_to, cost_usd FROM llm_call").fetchall()
    assert spend == [(None, "deleted", Decimal("0.020000"))]
    assert not kept["shot"].exists() and not storage.user_dir(other_id).exists()

    # The owner's own rows are untouched, and so are the shared facts about the jobs.
    for table in tenant.PRIVATE_TABLES:
        now = sql.execute(
            f"SELECT count(*) FROM {table} WHERE user_id <> %s", (other_id,)
        ).fetchone()
        assert now == owner_rows[table], table
    assert sql.execute("SELECT count(*) FROM listing").fetchone() == (1,)
    assert sql.execute("SELECT count(*) FROM posting").fetchone() == (1,)
    assert sql.execute("SELECT count(*) FROM company").fetchone() == (1,)
    # The owner still follows the company, so it stays on and its postings stay open.
    assert sql.execute("SELECT active FROM watched_board").fetchone() == (True,)
    assert sql.execute("SELECT state FROM posting").fetchone() == ("open",)
    assert (await api.get("/api/profile")).json()["full_name"] is None  # the owner is fine
    assert (await api.get("/api/listings")).json()["total"] == 1


async def test_the_last_follower_leaving_closes_the_company(
    other: httpx.AsyncClient, other_id: uuid.UUID, sql: psycopg.Connection[Any], tmp_path: Path
) -> None:
    """Nobody follows it once they are gone, so it stops being fetched and its postings are
    closed, exactly as unfollowing by hand does (pipeline/unfollow.py)."""
    await _belongings(other, sql, other_id, tmp_path)
    sql.execute("DELETE FROM board_follow WHERE user_id <> %s", (other_id,))
    assert sql.execute("SELECT active FROM watched_board").fetchone() == (True,)
    assert (await other.post("/api/account/delete", json=DELETE)).status_code == 200
    assert sql.execute("SELECT active FROM watched_board").fetchone() == (False,)
    assert sql.execute("SELECT state, close_reason FROM posting").fetchone() == (
        "closed",
        "unfollowed",
    )


async def test_the_frozen_rule_opens_for_erasure_and_nothing_else(
    other: httpx.AsyncClient, other_id: uuid.UUID, sql: psycopg.Connection[Any], tmp_path: Path
) -> None:
    """An Applied application cannot be deleted or edited (A2). Erasure is the one
    exception, and only inside its own transaction: app.erasing is set with SET LOCAL, so
    every other statement still meets the rule."""
    await _belongings(other, sql, other_id, tmp_path)
    app_id = sql.execute("SELECT id FROM application").fetchone()
    assert app_id is not None
    with pytest.raises(psycopg.errors.RaiseException):
        sql.execute("DELETE FROM application WHERE id = %s", (app_id[0],))
    with pytest.raises(psycopg.errors.RaiseException):
        sql.execute("UPDATE application SET answers = '{}' WHERE id = %s", (app_id[0],))

    assert (await other.post("/api/account/delete", json=DELETE)).status_code == 200
    assert sql.execute("SELECT count(*) FROM application").fetchone() == (0,)

    # The rule is back for everyone else, on a fresh Applied row.
    sql.execute(
        "INSERT INTO resume_version (file_path, file_sha256, original_name, mime)"
        " VALUES ('x.pdf', repeat('0', 64), 'x.pdf', 'application/pdf')"
    )
    sql.execute(
        "INSERT INTO application (listing_id, posting_id, resume_version_id, mode, status)"
        " SELECT id, primary_posting_id, (SELECT id FROM resume_version), 'review', 'applied'"
        " FROM listing"
    )
    with pytest.raises(psycopg.errors.RaiseException):
        sql.execute("DELETE FROM application")


async def test_delete_asks_for_the_password_and_the_address(
    other: httpx.AsyncClient, other_id: uuid.UUID, sql: psycopg.Connection[Any]
) -> None:
    wrong_password = await other.post(
        "/api/account/delete", json={**DELETE, "password": "not the password"}
    )
    assert wrong_password.status_code == 403
    wrong_email = await other.post(
        "/api/account/delete", json={**DELETE, "confirm_email": OWNER_EMAIL}
    )
    assert wrong_email.status_code == 422
    assert wrong_email.json()["error"]["code"] == "email_mismatch"
    assert _accounts(sql, other_id) == (1,)
    assert (await other.get("/api/profile")).status_code == 200  # still signed in


async def test_the_admin_account_cannot_be_deleted(
    api: httpx.AsyncClient, other_id: uuid.UUID
) -> None:
    refused = await api.post(
        "/api/account/delete", json={"password": OWNER_PASSWORD, "confirm_email": OWNER_EMAIL}
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "conflict"
    # And not through the admin's own route either, which deletes anybody else.
    me = (await api.get("/api/auth/me")).json()["id"]
    assert (await api.delete(f"/api/admin/accounts/{me}")).status_code == 409
    assert (await api.delete(f"/api/admin/accounts/{other_id}")).status_code == 200


async def test_the_address_can_sign_up_again_and_starts_empty(
    other: httpx.AsyncClient, other_id: uuid.UUID, anon: httpx.AsyncClient,
    sql: psycopg.Connection[Any],
) -> None:  # fmt: skip
    await other.put("/api/profile", json={"full_name": "Other Person"})
    assert (await other.post("/api/account/delete", json=DELETE)).status_code == 200
    again = await anon.post(
        "/api/auth/sign-up",
        json={
            "email": OTHER_EMAIL,
            "password": OTHER_PASSWORD,
            "accept_terms": True,
            "terms_version": TERMS_VERSION,
        },
    )
    assert again.status_code == 201  # a new account, waiting for its emailed link
    new_id = sql.execute("SELECT id FROM app_user WHERE email = %s", (OTHER_EMAIL,)).fetchone()
    assert new_id is not None and new_id[0] != other_id
    sql.execute("UPDATE app_user SET email_verified_at = now() WHERE id = %s", (new_id[0],))
    async with _client() as fresh:
        await _sign_in(fresh, OTHER_EMAIL, OTHER_PASSWORD)
        assert (await fresh.get("/api/profile")).json()["full_name"] is None


# Export --------------------------------------------------------------------------------


async def test_export_holds_everything_of_mine_and_nothing_of_yours(
    api: httpx.AsyncClient, other: httpx.AsyncClient, other_id: uuid.UUID,
    sql: psycopg.Connection[Any], tmp_path: Path,
) -> None:  # fmt: skip
    await api.put("/api/profile", json={"full_name": "Asha Rao"})
    await api.post("/api/answers", json={"question": "Owner only?", "answer": "yes"})
    kept = await _belongings(other, sql, other_id, tmp_path)

    resp = await other.get("/api/account/export")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(resp.content)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        files = {n: json.loads(zf.read(n)) for n in erasure.FILES.values()}
        readme = zf.read("README.txt").decode()

    # One file per private table, plus the files the rows point at and a README.
    assert set(erasure.FILES.values()) <= names
    assert f"files/screenshots/{kept['shot'].name}" in names
    assert any(n.startswith("files/") and n.endswith(".pdf") for n in names)
    assert OTHER_EMAIL in readme

    assert [p["full_name"] for p in files["profile.json"]] == ["Other Person"]
    assert [a["answer"] for a in files["answers.json"]] == ["30 days"]
    assert len(files["applications.json"]) == 1
    assert files["applications.json"][0]["job"]["company"] == "Acme"
    assert files["companies-followed.json"][0]["company"] == "Acme"
    assert files["ai-spend.json"][0]["cost_usd"] == 0.02
    # The cost is there; what was sent to the model never is.
    assert "input_sha256" not in files["ai-spend.json"][0]
    # Nothing of the owner's, in any file.
    everything = json.dumps(files)
    assert "Asha Rao" not in everything and "Owner only?" not in everything


async def test_export_and_delete_work_without_agreeing_to_a_new_notice(
    other: httpx.AsyncClient, other_id: uuid.UUID, sql: psycopg.Connection[Any]
) -> None:
    """Refusing the notice must not trap anyone: the rest of /api is closed to them, and
    these two stay open, or there would be no way to leave (S14, S15)."""
    sql.execute("UPDATE app_user SET terms_version = 'older' WHERE id = %s", (other_id,))
    assert (await other.get("/api/profile")).status_code == 403
    assert (await other.get("/api/account/export")).status_code == 200
    assert (await other.post("/api/account/delete", json=DELETE)).status_code == 200
    assert _accounts(sql, other_id) == (0,)


async def test_neither_route_answers_without_a_session(anon: httpx.AsyncClient) -> None:
    assert (await anon.get("/api/account/export")).status_code == 401
    assert (await anon.post("/api/account/delete", json=DELETE)).status_code == 401


async def test_the_erasure_flag_is_not_left_on(
    other: httpx.AsyncClient, other_id: uuid.UUID, sql: psycopg.Connection[Any], tmp_path: Path
) -> None:
    """erase() sets it with SET LOCAL and commits, so the connection goes back to refusing."""
    await _belongings(other, sql, other_id, tmp_path)
    with tenant.as_system():
        async with get_sessionmaker()() as session:
            await erasure.erase(session, other_id)
            left = await session.scalar(text("SELECT current_setting('app.erasing', true)"))
    assert left in (None, "", "off")
