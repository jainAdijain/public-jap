"""A user's own OpenRouter key pays for their own work (IDEA-19)."""

import base64
import os
import uuid
from typing import Any

import httpx
import psycopg
import pytest
from sqlalchemy import select

import llm.client as client
from database.models import LlmCall, UserSecret
from database.session import get_sessionmaker
from llm import call
from llm.redact import Sensitive
from llm.secrets import CannotOpen, byok_key, hint_of, open_sealed, seal
from llm.spend import month_spend
from schemas.resume import ParsedResume
from tests.test_llm_call import GOOD, Stub

KEY = "sk-or-v1-" + "a" * 40
OTHER_KEY = "sk-or-v1-" + "b" * 40


def set_master_key(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    """JAP_SECRET_KEY for the rest of this test. The settings are cached, so the cache
    goes too; the `db` fixture clears it the same way when it swaps the data folder."""
    from config import get_settings

    if value is None:
        monkeypatch.delenv("JAP_SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("JAP_SECRET_KEY", value)
    get_settings.cache_clear()


def a_master_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture(autouse=True)
def master_key(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A master key for this stack. It takes `db` so it runs after it: `db` clears the
    settings cache, which would otherwise throw this away."""
    set_master_key(monkeypatch, a_master_key())


# --- the sealing itself -------------------------------------------------------------------


def test_a_sealed_key_opens_only_for_its_owner(db: str) -> None:
    mine, yours = uuid.uuid4(), uuid.uuid4()
    ciphertext, nonce = seal(KEY, mine, "openrouter")
    assert KEY.encode() not in ciphertext  # not sitting there in the clear
    assert open_sealed(ciphertext, nonce, mine, "openrouter") == KEY
    # The owner and the kind are bound in, so a row moved to another account will not open.
    with pytest.raises(CannotOpen):
        open_sealed(ciphertext, nonce, yours, "openrouter")


def test_two_seals_of_one_key_differ(db: str) -> None:
    """A fresh nonce every time, so equal keys do not give equal ciphertext."""
    user_id = uuid.uuid4()
    first, _ = seal(KEY, user_id, "openrouter")
    second, _ = seal(KEY, user_id, "openrouter")
    assert first != second


def test_the_hint_is_only_the_last_four() -> None:
    assert hint_of(KEY) == KEY[-4:] and len(hint_of(KEY)) == 4
    assert hint_of("abc") == ""


async def test_an_unreadable_key_falls_back_instead_of_failing(
    owner: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The master key changed, so the saved key cannot be opened. The call still goes out
    on JAP's key rather than the whole search dying."""
    async with get_sessionmaker()() as session:
        ciphertext, nonce = seal(KEY, owner, "openrouter")
        session.add(
            UserSecret(user_id=owner, kind="openrouter", ciphertext=ciphertext, nonce=nonce)
        )
        await session.commit()
    set_master_key(monkeypatch, a_master_key())
    async with get_sessionmaker()() as session:
        assert await byok_key(session, owner) is None


# --- the API ----------------------------------------------------------------------------


async def test_save_show_and_remove_my_key(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    before = (await api.get("/api/profile/api-key")).json()
    assert before == {"available": True, "set": False, "hint": "", "saved_at": None}

    saved = await api.put("/api/profile/api-key", json={"api_key": KEY})
    assert saved.status_code == 200
    body = saved.json()
    assert body["set"] is True and body["hint"] == KEY[-4:] and body["saved_at"] is not None
    # Only the hint ever comes back: the key itself is not in any reply.
    assert KEY not in saved.text and KEY not in (await api.get("/api/profile/api-key")).text
    # Nor is it in the database in the clear.
    stored = sql.execute("SELECT ciphertext, hint FROM user_secret").fetchone()
    assert stored is not None and KEY.encode() not in bytes(stored[0])
    assert stored[1] == KEY[-4:]

    replaced = (await api.put("/api/profile/api-key", json={"api_key": OTHER_KEY})).json()
    assert replaced["hint"] == OTHER_KEY[-4:]
    assert sql.execute("SELECT count(*) FROM user_secret").fetchone() == (1,)

    gone = (await api.delete("/api/profile/api-key")).json()
    assert gone["set"] is False and gone["hint"] == ""
    assert sql.execute("SELECT count(*) FROM user_secret").fetchone() == (0,)


async def test_a_key_that_is_not_one_is_refused(api: httpx.AsyncClient) -> None:
    assert (await api.put("/api/profile/api-key", json={"api_key": "short"})).status_code == 422
    assert (await api.put("/api/profile/api-key", json={"api_key": " " * 20})).status_code == 422


async def test_without_a_master_key_nothing_can_be_saved(
    api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stack with no JAP_SECRET_KEY says so rather than storing keys it could not read
    back after a restart. The screen hides the card on `available`."""
    set_master_key(monkeypatch, None)
    assert (await api.get("/api/profile/api-key")).json()["available"] is False
    refused = await api.put("/api/profile/api-key", json={"api_key": KEY})
    assert refused.status_code == 503 and refused.json()["error"]["code"] == "secrets_unavailable"


async def test_my_key_is_mine_alone(
    api: httpx.AsyncClient, other: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """Row-level security: the second account sees no key and setting one does not touch
    the first account's (PRD S7)."""
    await api.put("/api/profile/api-key", json={"api_key": KEY})
    assert (await other.get("/api/profile/api-key")).json()["set"] is False
    await other.put("/api/profile/api-key", json={"api_key": OTHER_KEY})
    hints = sorted(r[0] for r in sql.execute("SELECT hint FROM user_secret").fetchall())
    assert hints == sorted([KEY[-4:], OTHER_KEY[-4:]])
    assert (await api.get("/api/profile/api-key")).json()["hint"] == KEY[-4:]


# --- what it changes about a model call ---------------------------------------------------


async def test_my_own_key_is_used_and_billed_to_me_not_to_jap(
    owner: uuid.UUID, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await api.put("/api/profile/api-key", json={"api_key": KEY})
    stub = Stub([GOOD])
    seen: list[str] = []

    async def spy(api_key: str, body: dict[str, Any], wait_s: float = 0) -> Any:
        seen.append(api_key)
        return await stub(api_key, body, wait_s)

    monkeypatch.setattr(client, "chat", spy)
    async with get_sessionmaker()() as session:
        await call(
            session, "resume_parse", {"resume_text": "Python"}, ParsedResume, sensitive=Sensitive()
        )
    assert seen == [KEY]  # their key went out, not the service's
    async with get_sessionmaker()() as session:
        row = (await session.scalars(select(LlmCall))).one()
        assert row.billed_to == "byok" and row.user_id == owner
        # Their own money, so it is not counted against the JAP allowance.
        assert (await month_spend(session, owner)).month_usd == 0.0


async def test_without_a_key_the_call_is_billed_to_the_allowance(
    owner: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client, "chat", Stub([GOOD]))
    async with get_sessionmaker()() as session:
        await call(
            session, "resume_parse", {"resume_text": "Python"}, ParsedResume, sensitive=Sensitive()
        )
        row = (await session.scalars(select(LlmCall))).one()
        assert row.billed_to == "user"
        assert (await month_spend(session, owner)).month_usd == 0.001


async def test_my_own_key_is_not_stopped_by_the_allowance(
    owner: uuid.UUID, api: httpx.AsyncClient, sql: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """The allowance is JAP's money. Someone spending their own is not held to it, and
    their spending does not use up the service's budget either."""
    await api.put("/api/profile/api-key", json={"api_key": KEY})
    sql.execute("UPDATE profile SET spend_cap_usd = 0")  # allowance fully used up
    monkeypatch.setattr(client, "chat", Stub([GOOD]))
    async with get_sessionmaker()() as session:
        await call(
            session, "resume_parse", {"resume_text": "Python"}, ParsedResume, sensitive=Sensitive()
        )
        row = (await session.scalars(select(LlmCall))).one()
        assert row.billed_to == "byok" and row.error is None
    # jap_spend() leaves byok out, so neither the shared budget nor the total sees it.
    total = sql.execute("SELECT jap_spend(now() - interval '1 day', false)").fetchone()
    assert total == (0,)
