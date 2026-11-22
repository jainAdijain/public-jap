"""Every account can finish a form that asks for a security code (IDEA-16).

The three routes a code can arrive by, and the rule that makes applying side by side safe:
a code belongs to the account it was addressed to, never to whoever happened to submit
last. The mailbox half is in test_security_code.py; this is the application and the API.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from config import get_settings
from database.session import get_sessionmaker
from pipeline.apply import security_code as sc
from tests.test_apply import FORM, _fake_form, _listing, _prepare, _profile, _resume

SECRET = "an-inbound-secret"
CODE_MAIL = "Your code is: Ab3dE5fG. It expires in 10 minutes."


@pytest.fixture
def setup(db: str, sql: psycopg.Connection[Any], tmp_path: Path) -> dict[str, Any]:
    """A profile, a resume and a listing to apply to, as test_apply.py sets it up."""
    _profile(sql)
    return {"resume": _resume(sql, tmp_path), "listing": _listing(sql), "data": tmp_path}


def _applying(sql: psycopg.Connection[Any], app_id: str, wanted: bool = True) -> None:
    """The row as it is while the browser holds the form open, waiting for a code."""
    sql.execute(
        "UPDATE application SET status = 'applying', code_wanted_at = %s WHERE id = %s",
        (datetime.now(UTC) if wanted else None, app_id),
    )


# --- route C: the person types it in ------------------------------------------------------


async def test_a_typed_code_reaches_the_waiting_form(
    setup: dict[str, Any], sql: psycopg.Connection[Any], api: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(setup["listing"])
    _applying(sql, app_id)

    shown = (await api.get(f"/api/applications/{app_id}")).json()
    assert shown["code_wanted"] is True  # the screen knows to ask

    typed = await api.post(f"/api/applications/{app_id}/security-code", json={"code": "Ab3dE5fG"})
    assert typed.status_code == 200
    # It is parked on the row for the waiting browser, and never comes back out.
    assert "Ab3dE5fG" not in typed.text
    assert await sc.typed_code(app_id) == "Ab3dE5fG"


async def test_a_code_is_only_taken_while_a_form_is_waiting(
    setup: dict[str, Any], sql: psycopg.Connection[Any], api: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(setup["listing"])
    nothing_asked = await api.post(
        f"/api/applications/{app_id}/security-code", json={"code": "Ab3dE5fG"}
    )
    assert nothing_asked.status_code == 409
    _applying(sql, app_id)
    for bad in ("ab", "has spaces", "!!!!!!!!", "x" * 20):
        resp = await api.post(f"/api/applications/{app_id}/security-code", json={"code": bad})
        assert resp.status_code == 422, bad


async def test_the_waiter_takes_a_typed_code_without_any_mailbox(
    setup: dict[str, Any], sql: psycopg.Connection[Any], api: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """A stack with no mailbox at all still finishes the form: reads_mail is off and the
    waiter watches only the row."""
    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(setup["listing"])
    _applying(sql, app_id)
    await api.post(f"/api/applications/{app_id}/security-code", json={"code": "Ab3dE5fG"})
    get_code = sc.waiter("greenhouse", app_id, None, reads_mail=False, wait_s=1, poll_s=0)
    assert await get_code(datetime.now(UTC), 8) == "Ab3dE5fG"


async def test_the_waiter_marks_the_application_as_asking(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing has asked yet, so the screen shows no box until the waiter starts."""
    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(setup["listing"])
    _applying(sql, app_id, wanted=False)
    get_code = sc.waiter("greenhouse", app_id, None, reads_mail=False, wait_s=0, poll_s=0)
    assert await get_code(datetime.now(UTC), 8) is None
    wanted = sql.execute(
        "SELECT code_wanted_at FROM application WHERE id = %s", (app_id,)
    ).fetchone()
    assert wanted is not None and wanted[0] is not None


# --- route B: an inbound-email service ----------------------------------------------------


@pytest.fixture
def inbound_on(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAP_INBOUND_SECRET", SECRET)
    get_settings.cache_clear()


async def test_inbound_mail_gives_the_code_to_the_right_account(
    setup: dict[str, Any], sql: psycopg.Connection[Any], api: httpx.AsyncClient,
    anon: httpx.AsyncClient, inbound_on: None, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(setup["listing"])
    _applying(sql, app_id)
    assert (await api.post("/api/profile/code-address")).status_code == 200
    token = sql.execute("SELECT code_token FROM app_user WHERE code_token IS NOT NULL").fetchone()
    assert token is not None

    posted = await anon.post(
        "/api/inbound/email",
        json={"to": f"jap+{token[0]}@example.com", "text": CODE_MAIL},
        headers={"X-JAP-Inbound": SECRET},
    )
    assert posted.status_code == 200 and posted.json() == {"taken": True}
    assert await sc.typed_code(app_id) == "Ab3dE5fG"


async def test_inbound_mail_for_a_stranger_is_refused(
    anon: httpx.AsyncClient, inbound_on: None
) -> None:
    unknown = await anon.post(
        "/api/inbound/email",
        json={"to": "jap+nobodyhere@example.com", "text": CODE_MAIL},
        headers={"X-JAP-Inbound": SECRET},
    )
    assert unknown.status_code == 404
    no_token = await anon.post(
        "/api/inbound/email",
        json={"to": "jap@example.com", "text": CODE_MAIL},
        headers={"X-JAP-Inbound": SECRET},
    )
    assert no_token.status_code == 422


async def test_inbound_needs_the_secret(anon: httpx.AsyncClient, inbound_on: None) -> None:
    body: dict[str, Any] = {"to": "jap+abc@example.com", "text": CODE_MAIL}
    assert (await anon.post("/api/inbound/email", json=body)).status_code == 401
    wrong = await anon.post("/api/inbound/email", json=body, headers={"X-JAP-Inbound": "nope"})
    assert wrong.status_code == 401


async def test_inbound_is_off_without_a_secret(db: str, anon: httpx.AsyncClient) -> None:
    """No JAP_INBOUND_SECRET: the route answers as though it does not exist, so nothing
    is open by default on a stack that never configured it."""
    resp = await anon.post(
        "/api/inbound/email",
        json={"to": "jap+abc@example.com", "text": CODE_MAIL},
        headers={"X-JAP-Inbound": ""},
    )
    assert resp.status_code == 404


# --- route A: the forwarding address ------------------------------------------------------


@pytest.fixture
def mailbox_set(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_USERNAME", "jap@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    get_settings.cache_clear()


async def test_each_account_gets_its_own_address(
    api: httpx.AsyncClient, other: httpx.AsyncClient, mailbox_set: None
) -> None:
    assert (await api.get("/api/profile/code-address")).json()["address"] is None
    mine = (await api.post("/api/profile/code-address")).json()["address"]
    yours = (await other.post("/api/profile/code-address")).json()["address"]
    assert mine and yours and mine != yours
    assert mine.startswith("jap+") and mine.endswith("@example.com")
    # Asking twice keeps the address: a mail filter is set up once.
    assert (await api.post("/api/profile/code-address")).json()["address"] == mine
    assert (await api.get("/api/profile/code-address")).json()["address"] == mine


async def test_with_no_mailbox_there_is_no_address_to_forward_to(
    api: httpx.AsyncClient,
) -> None:
    """The token is still minted — an inbound-email service needs only that — but there
    is no address to show, so the screen offers typing the code instead."""
    made = (await api.post("/api/profile/code-address")).json()
    assert made == {"address": None, "mailbox": False}


# --- the code never outlives the form it was for ------------------------------------------


async def test_the_code_is_gone_once_the_attempt_ends(
    setup: dict[str, Any], sql: psycopg.Connection[Any], api: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """It is a credential with a job to do, and the job is over."""
    from pipeline.apply import flow

    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(setup["listing"])
    _applying(sql, app_id)
    await api.post(f"/api/applications/{app_id}/security-code", json={"code": "Ab3dE5fG"})
    async with get_sessionmaker()() as session:
        await flow.fail_by_id(session, app_id, "The site did not take the code")
    left = sql.execute(
        "SELECT security_code, code_wanted_at FROM application WHERE id = %s", (app_id,)
    ).fetchone()
    assert left == (None, None)


def test_the_code_is_left_out_of_an_export() -> None:
    from pipeline import erasure

    assert "security_code" in erasure.LEAVE_OUT["application"]
