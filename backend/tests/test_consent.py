"""What a user agreed to, and what JAP refuses without it (PRD S14).

Three agreements, checked here one at a time: the terms and the privacy notice, ticked at
sign-up and asked for again when the notice changes; JAP applying in someone's name; and
JAP doing that without asking first. Every other test file works as an established account
that has agreed to all three (tests/conftest.py, tests/mine.py); this one is where their
absence is the point.
"""

import uuid
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from auth import consent
from database.session import get_sessionmaker
from pipeline.apply import flow
from pipeline.apply.form import SubmitResult
from tests import mine
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, _client, _sign_in, add_account
from tests.test_apply import FORM, _fake_form, _listing, _prepare, _resume, _status

OLD_VERSION = "2000-01-01"
TICK = {"accept_terms": True, "terms_version": consent.TERMS_VERSION}
LATER = {"email": "later@example.com", "password": "a long enough password"}


def _profile(sql: psycopg.Connection[Any]) -> None:
    """A filled-in profile that has agreed to nothing: the contact details JAP needs to
    fill a form, and neither opt-in."""
    sql.execute(
        "INSERT INTO profile (full_name, email, phone, target_roles, locations, daily_cap)"
        " VALUES ('Asha Rao', 'asha.rao@example.com', '+91 98765 43210',"
        " '{Backend Engineer}', '{Bangalore}', 20)"
    )


@pytest.fixture
def ready(db: str, sql: psycopg.Connection[Any], tmp_path: Path) -> dict[str, Any]:
    """Everything an application needs except the opt-in to send one."""
    _profile(sql)
    return {"resume": _resume(sql, tmp_path), "listing": _listing(sql), "data": tmp_path}


# The terms and the privacy notice ------------------------------------------------------


async def test_the_first_account_agrees_as_it_is_made(anon: httpx.AsyncClient) -> None:
    body = {"email": OWNER_EMAIL, "password": OWNER_PASSWORD, **TICK}
    first = await anon.post("/api/auth/sign-up", json=body)
    assert first.status_code == 201 and first.json()["user"]["terms_accepted"] is True
    assert (await anon.get("/api/auth/state")).json()["terms_version"] == consent.TERMS_VERSION
    assert (await anon.get("/api/profile")).status_code == 200


async def test_sign_up_needs_the_tick_and_the_notice_it_showed(
    api: httpx.AsyncClient, anon: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """An unticked box is refused, and so is a tick against a notice that has since
    changed: the page sends back the version it displayed."""
    await api.post("/api/auth/sign-out")
    for body in (
        LATER,  # the field left out altogether
        {**LATER, "accept_terms": False, "terms_version": consent.TERMS_VERSION},
        {**LATER, "accept_terms": True, "terms_version": OLD_VERSION},
        {**LATER, "accept_terms": True, "terms_version": ""},
    ):
        resp = await anon.post("/api/auth/sign-up", json=body)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "terms_not_accepted"
        assert resp.json()["error"]["message"] == consent.NEEDS_TERMS
    assert sql.execute("SELECT count(*) FROM app_user").fetchone() == (1,)  # only the owner
    assert (await anon.post("/api/auth/sign-up", json={**LATER, **TICK})).status_code == 201
    row = sql.execute(
        "SELECT terms_version, terms_accepted_at IS NOT NULL FROM app_user WHERE email = %s",
        (LATER["email"],),
    ).fetchone()
    assert row == (consent.TERMS_VERSION, True)  # agreed as the account was made


async def test_a_notice_nobody_agreed_to_asks_before_anything_else(
    db: str, fast_hashing: None
) -> None:
    """An account from before there was a notice, and one from before the current one, are
    both asked again: until they tick, /api answers terms_not_accepted. The migration
    backfills nothing on purpose, so this is what every existing account meets."""
    add_account(db, OWNER_EMAIL, OWNER_PASSWORD, admin=True, consented=False)
    async with _client() as client:
        await _sign_in(client, OWNER_EMAIL, OWNER_PASSWORD)
        state = (await client.get("/api/auth/state")).json()
        assert state["user"]["terms_accepted"] is False
        assert state["terms_version"] == consent.TERMS_VERSION
        for path in ("/api/profile", "/api/listings", "/api/boards", "/api/admin/accounts"):
            refused = await client.get(path)
            assert refused.status_code == 403, path
            assert refused.json()["error"]["code"] == "terms_not_accepted"
        # A tick against some other notice is not this one.
        old = await client.post("/api/auth/terms", json={"terms_version": OLD_VERSION})
        assert old.status_code == 422 and old.json()["error"]["code"] == "terms_not_accepted"
        assert (await client.get("/api/profile")).status_code == 403

        agreed = await client.post("/api/auth/terms", json={"terms_version": consent.TERMS_VERSION})
        assert agreed.status_code == 200 and agreed.json()["terms_accepted"] is True
        assert (await client.get("/api/profile")).status_code == 200
        assert (await client.get("/api/admin/accounts")).status_code == 200


async def test_a_newer_notice_asks_an_agreed_account_again(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """Raising TERMS_VERSION is what asks everyone again: the stored version no longer
    matches, so the session that was fine a moment ago is refused until it ticks."""
    assert (await api.get("/api/profile")).status_code == 200
    sql.execute("UPDATE app_user SET terms_version = %s", (OLD_VERSION,))
    refused = await api.get("/api/profile")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "terms_not_accepted"
    assert (await api.get("/api/auth/me")).json()["terms_accepted"] is False  # /auth is open
    await api.post("/api/auth/terms", json={"terms_version": consent.TERMS_VERSION})
    assert (await api.get("/api/profile")).status_code == 200


async def test_the_tick_at_sign_up_counts_for_an_account_still_waiting_for_its_link(
    api: httpx.AsyncClient, anon: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """Signing up again while the link is waiting is the same tick, so the account is not
    asked a second time the moment it opens the link."""
    await api.post("/api/auth/sign-out")
    assert (await anon.post("/api/auth/sign-up", json={**LATER, **TICK})).status_code == 201
    sql.execute("UPDATE app_user SET terms_version = %s WHERE email = %s",
                (OLD_VERSION, LATER["email"]))  # fmt: skip
    assert (await anon.post("/api/auth/sign-up", json={**LATER, **TICK})).status_code == 201
    row = sql.execute(
        "SELECT terms_version FROM app_user WHERE email = %s", (LATER["email"],)
    ).fetchone()
    assert row == (consent.TERMS_VERSION,)


# JAP applying in my name ---------------------------------------------------------------


async def test_nothing_is_prepared_without_the_opt_in(
    ready: dict[str, Any], api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """Apply says why rather than preparing, and a Find and apply run picks nothing."""
    listing = ready["listing"]
    detail = (await api.get(f"/api/listings/{listing}")).json()
    assert detail["apply_blocked"] == consent.NEEDS_SUBMIT
    blocked = await api.post(f"/api/listings/{listing}/apply")
    assert blocked.status_code == 409 and blocked.json()["error"]["message"] == consent.NEEDS_SUBMIT

    run = (await api.post("/api/runs", json={"mode": "find_and_apply"})).json()
    async with get_sessionmaker()() as session:
        assert await flow.pick(session, run["id"]) == []
    assert sql.execute("SELECT count(*) FROM application").fetchone() == (0,)

    agreed = await api.patch("/api/profile", json={"submit_opt_in": True})
    assert agreed.status_code == 200 and agreed.json()["submit_opt_in"] is True
    assert (await api.get(f"/api/listings/{listing}")).json()["apply_blocked"] is None
    assert (await api.post(f"/api/listings/{listing}/apply")).status_code == 202
    async with get_sessionmaker()() as session:
        assert await flow.pick(session, run["id"]) == [listing]


async def test_taking_the_opt_in_back_stops_an_attempt_that_is_waiting(
    ready: dict[str, Any], api: httpx.AsyncClient, sql: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """Submit asks again at the last moment: an attempt approved before the opt-in was
    taken back is failed rather than sent."""
    await api.patch("/api/profile", json={"submit_opt_in": True})
    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(ready["listing"])
    assert _status(sql, app_id) == ("ready_for_review", "ready_for_review")
    assert (await api.post(f"/api/applications/{app_id}/approve")).status_code == 200

    async def must_not_run(*args: Any) -> SubmitResult:
        raise AssertionError("submitted after the opt-in was taken back")

    monkeypatch.setattr(flow, "submit_form", must_not_run)
    taken_back = await api.patch("/api/profile", json={"submit_opt_in": False})
    assert taken_back.json() == {**taken_back.json(), "submit_opt_in": False, "auto_opt_in": False}
    async with get_sessionmaker()() as session:
        assert await flow.submit(session, app_id) == {"status": "failed"}
    row = sql.execute("SELECT error, submit_started_at FROM application WHERE id = %s",
                      (app_id,)).fetchone()  # fmt: skip
    assert row == (consent.NEEDS_SUBMIT, None)  # Submit was never pressed
    assert _status(sql, app_id) == ("failed", "failed")


# JAP applying without asking first -----------------------------------------------------


async def test_automatic_mode_needs_its_own_agreement(api: httpx.AsyncClient) -> None:
    refused = await api.patch("/api/profile", json={"apply_mode": "automatic"})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "consent_needed"
    assert refused.json()["error"]["message"] == consent.NEEDS_AUTO
    # The wider opt-in on its own is still not enough: sending unseen is its own agreement.
    only_submit = await api.patch("/api/profile", json={"submit_opt_in": True})
    assert only_submit.json()["apply_mode"] == "review"
    assert (await api.patch("/api/profile", json={"apply_mode": "automatic"})).status_code == 409

    both = await api.patch("/api/profile", json={"auto_opt_in": True, "apply_mode": "automatic"})
    assert both.json()["apply_mode"] == "automatic" and both.json()["auto_opt_in"] is True


async def test_taking_back_the_wider_opt_in_takes_the_narrower_one_with_it(
    api: httpx.AsyncClient,
) -> None:
    """Permission to send unseen cannot outlive permission to send at all, so the account
    goes back to Review mode rather than quietly staying in Automatic."""
    await api.patch(
        "/api/profile",
        json={"submit_opt_in": True, "auto_opt_in": True, "apply_mode": "automatic"},
    )
    back = (await api.patch("/api/profile", json={"submit_opt_in": False})).json()
    assert back["submit_opt_in"] is False and back["auto_opt_in"] is False
    assert back["apply_mode"] == "review"


async def test_a_run_in_automatic_mode_picks_nothing_without_the_second_opt_in(
    ready: dict[str, Any], api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """apply_mode set straight in the database (as an older row could be) still sends
    nothing: pick asks for the agreement, not only for the mode."""
    mine.opt_in(sql, automatic=False)
    sql.execute("UPDATE profile SET apply_mode = 'automatic'")
    run = (await api.post("/api/runs", json={"mode": "find_and_apply"})).json()
    async with get_sessionmaker()() as session:
        assert await flow.pick(session, run["id"]) == []
    mine.opt_in(sql)
    async with get_sessionmaker()() as session:
        assert await flow.pick(session, run["id"]) == [ready["listing"]]


async def test_the_opt_ins_are_one_account_s_own(
    db: str, api: httpx.AsyncClient, other: httpx.AsyncClient, other_id: uuid.UUID
) -> None:
    """Consent is personal (S7): one account agreeing says nothing about another."""
    await api.patch("/api/profile", json={"submit_opt_in": True, "auto_opt_in": True})
    theirs = (await other.get("/api/profile")).json()
    assert theirs["submit_opt_in"] is False and theirs["auto_opt_in"] is False
    assert (await other.patch("/api/profile", json={"apply_mode": "automatic"})).status_code == 409
