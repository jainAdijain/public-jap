"""The launch switches (docs/intent/launch-plan.md, decisions D1, D2, D4).

D1: no account gets AI on JAP's key unless the admin grants it; an account's own
OpenRouter key always works. D2: Automatic mode is the admin's to allow, account by account.
D4: while sign-up is paused, only an address on the admin's invite list may sign up
(tests/test_sign_up.py and tests/test_google.py have the sign-up side).
"""

import uuid

import httpx
import pytest

import llm.client as client
from database import tenant
from database.session import get_sessionmaker
from llm import NoAiCredit, call
from llm.call import NO_AI_CREDIT
from llm.redact import Sensitive
from pipeline.limits import PLUS, plans
from schemas.resume import ParsedResume
from tests.test_byok import KEY, a_master_key, set_master_key
from tests.test_llm_call import GOOD, Stub


async def _parse(user_id: uuid.UUID) -> None:
    with tenant.as_user(user_id):
        async with get_sessionmaker()() as session:
            await call(
                session, "resume_parse", {"resume_text": "Python"}, ParsedResume,
                sensitive=Sensitive(),
            )  # fmt: skip


async def test_a_new_account_gets_no_ai_on_japs_key_until_the_admin_grants_it(
    api: httpx.AsyncClient,
    other: httpx.AsyncClient,
    other_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = Stub([GOOD] * 4)
    monkeypatch.setattr(client, "chat", stub)
    profile = (await other.get("/api/profile")).json()
    assert profile["ai_source"] == "none" and profile["spend"]["cap_usd"] == 0.0
    with pytest.raises(NoAiCredit, match="add your own OpenRouter key in Settings"):
        await _parse(other_id)
    assert stub.bodies == []  # refused before any model was asked

    granted = await api.put(f"/api/admin/accounts/{other_id}/limits", json={"ai_usd": 3})
    assert granted.json()["accounts"][1]["ai_source"] == "jap"
    await _parse(other_id)
    assert len(stub.bodies) == 1


async def test_an_accounts_own_key_works_with_no_grant(
    db: str,
    api: httpx.AsyncClient,
    other: httpx.AsyncClient,
    other_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_master_key(monkeypatch, a_master_key())
    stub = Stub([GOOD])
    monkeypatch.setattr(client, "chat", stub)
    assert (await other.put("/api/profile/api-key", json={"api_key": KEY})).status_code == 200
    assert (await other.get("/api/profile")).json()["ai_source"] == "own_key"
    listed = (await api.get("/api/admin/accounts")).json()["accounts"][1]
    assert listed["ai_source"] == "own_key"
    await _parse(other_id)
    assert len(stub.bodies) == 1


def test_no_plan_includes_ai_on_japs_key(db: str) -> None:
    """Plus sells companies and applications, not AI (decisions D1 and D3)."""
    assert {code: plan.ai_usd for code, plan in plans().items()} == {"free": 0.0, PLUS: 0.0}
    assert NO_AI_CREDIT.startswith("This account has no AI credit")


async def test_automatic_mode_is_the_admins_to_allow_account_by_account(
    api: httpx.AsyncClient, other: httpx.AsyncClient, other_id: uuid.UUID, owner: uuid.UUID
) -> None:
    agree = {"submit_opt_in": True, "auto_opt_in": True}
    assert (await other.patch("/api/profile", json=agree)).status_code == 200
    mine = (await other.get("/api/profile")).json()
    assert mine["automatic_allowed"] is False
    refused = await other.patch("/api/profile", json={"apply_mode": "automatic"})
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "automatic_not_allowed"

    path = f"/api/admin/accounts/{other_id}/automatic"
    assert (await other.put(path, json={"allowed": True})).status_code == 404  # admin only
    allowed = (await api.put(path, json={"allowed": True})).json()
    assert allowed["accounts"][1]["may_automatic"] is True
    switched = await other.patch("/api/profile", json={"apply_mode": "automatic"})
    assert switched.status_code == 200 and switched.json()["apply_mode"] == "automatic"

    # Taking it away puts the account back on Review at once.
    await api.put(path, json={"allowed": False})
    after = (await other.get("/api/profile")).json()
    assert after["apply_mode"] == "review" and after["automatic_allowed"] is False

    own = await api.put(f"/api/admin/accounts/{owner}/automatic", json={"allowed": False})
    assert own.status_code == 409  # the admin always may


async def test_the_invite_list_is_the_admins_and_refuses_an_address_with_an_account(
    api: httpx.AsyncClient, other: httpx.AsyncClient
) -> None:
    from tests.conftest import OTHER_EMAIL

    assert (
        await other.post("/api/admin/invites", json={"email": "a@example.com"})
    ).status_code == 404
    added = (await api.post("/api/admin/invites", json={"email": "Friend@Example.com"})).json()
    assert added["invites"] == ["friend@example.com"]
    taken = await api.post("/api/admin/invites", json={"email": OTHER_EMAIL.upper()})
    assert taken.status_code == 409
    gone = await api.post("/api/admin/invites/remove", json={"email": "friend@example.com"})
    assert gone.json()["invites"] == []
