"""Open sign-up (PRD S1, S10, S12): anyone can create an account, confirms the address by an
emailed link, and lands in an empty workspace of their own. Tries are limited, a human can
be asked for, and the admin can pause sign-up or turn an account off."""

import re
import uuid
from typing import Any

import httpx
import psycopg
import pytest

from auth import turnstile
from auth.consent import TERMS_VERSION
from config import get_settings
from pipeline.notify import Mail
from tests.conftest import OTHER_EMAIL, OTHER_PASSWORD, OWNER_EMAIL, OWNER_PASSWORD, _libpq

PASSWORD = "a long enough password"
# The terms and privacy tick every sign-up carries (S14). test_consent.py is the one that
# leaves it out, or sends an older notice's version, on purpose.
TICK: dict[str, Any] = {"accept_terms": True, "terms_version": TERMS_VERSION}
NEW: dict[str, Any] = {"email": "new@example.com", "password": PASSWORD, **TICK}


@pytest.fixture
def mails(monkeypatch: pytest.MonkeyPatch) -> list[Mail]:
    sent: list[Mail] = []

    async def fake_send(mail: Mail) -> None:
        sent.append(mail)

    monkeypatch.setattr("api.auth.send_mail", fake_send)
    return sent


def _token(mail: Mail) -> str:
    found = re.search(r"\?verify=([\w-]+)", mail.body)
    assert found is not None
    return found.group(1)


async def test_sign_up_verify_and_an_empty_workspace(
    api: httpx.AsyncClient, other: httpx.AsyncClient, mails: list[Mail]
) -> None:
    """A new visitor signs up, gets the link, opens it, and has nothing of anyone else's."""
    await api.put("/api/profile", json={"full_name": "Asha Rao", "target_roles": ["Engineer"]})
    await api.post("/api/answers", json={"question": "Notice period?", "answer": "30 days"})
    await api.post(
        "/api/boards", json={"ats": "greenhouse", "board_token": "acme", "display_name": "Acme"}
    )

    from tests.conftest import _client

    async with _client() as new:
        resp = await new.post("/api/auth/sign-up", json=NEW)
        assert resp.status_code == 201
        assert resp.json() == {
            "signed_in": False, "two_step": False, "user": None, "verify_email": True
        }  # fmt: skip
        assert [m.to for m in mails] == ["new@example.com"]
        assert (await new.get("/api/profile")).status_code == 401  # no session yet

        early = await new.post("/api/auth/sign-in", json=NEW)
        assert early.status_code == 403 and early.json()["error"]["code"] == "email_unverified"

        token = _token(mails[0])
        done = await new.post("/api/auth/verify", json={"token": token})
        assert done.status_code == 200 and done.json()["signed_in"] is True
        assert done.json()["user"]["email"] == "new@example.com"
        assert done.json()["user"]["is_admin"] is False

        profile = (await new.get("/api/profile")).json()
        assert profile["full_name"] is None and profile["target_roles"] == []
        assert (await new.get("/api/answers")).json() == []
        assert (await new.get("/api/boards")).json() == []  # not even the admin's companies
        assert (await new.get("/api/resumes")).json() == []
        assert (await new.get("/api/listings")).json()["total"] == 0
        assert (await new.get("/api/runs")).json() == []

        again = await new.post("/api/auth/verify", json={"token": token})
        assert again.status_code == 400  # the link works once
        await new.post("/api/auth/sign-out")
        assert (await new.post("/api/auth/sign-in", json=NEW)).status_code == 200
    assert (await api.get("/api/profile")).json()["full_name"] == "Asha Rao"  # untouched


async def test_sign_up_says_the_same_for_a_taken_address(
    api: httpx.AsyncClient, anon: httpx.AsyncClient, sql: psycopg.Connection[Any],
    mails: list[Mail],
) -> None:  # fmt: skip
    await api.post("/api/auth/sign-out")
    taken = await anon.post(
        "/api/auth/sign-up", json={"email": OWNER_EMAIL.upper(), "password": PASSWORD, **TICK}
    )
    fresh = await anon.post("/api/auth/sign-up", json=NEW)
    assert taken.status_code == fresh.status_code == 201 and taken.json() == fresh.json()
    assert [m.to for m in mails] == ["new@example.com"]  # nothing goes to the taken address
    assert sql.execute("SELECT count(*) FROM app_user").fetchone() == (2,)
    # The owner's password was not replaced by the second sign-up.
    ok = await anon.post(
        "/api/auth/sign-in", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    )
    assert ok.status_code == 200

    # Signing up again while the link is waiting sends no second email inside 2 minutes.
    await anon.post("/api/auth/sign-out")
    await anon.post("/api/auth/sign-up", json=NEW)
    resend = await anon.post("/api/auth/verify/resend", json={"email": NEW["email"]})
    unknown = await anon.post("/api/auth/verify/resend", json={"email": "nobody@example.com"})
    assert resend.status_code == unknown.status_code == 202 and resend.json() == unknown.json()
    assert len(mails) == 1
    sql.execute("UPDATE email_verification SET created_at = now() - interval '3 minutes'")
    await anon.post("/api/auth/verify/resend", json={"email": NEW["email"]})
    assert len(mails) == 2 and _token(mails[1]) != _token(mails[0])


async def test_a_verify_link_expires(
    api: httpx.AsyncClient, anon: httpx.AsyncClient, sql: psycopg.Connection[Any],
    mails: list[Mail],
) -> None:  # fmt: skip
    await api.post("/api/auth/sign-out")
    await anon.post("/api/auth/sign-up", json=NEW)
    sql.execute("UPDATE email_verification SET expires_at = now() - interval '1 minute'")
    late = await anon.post("/api/auth/verify", json={"token": _token(mails[0])})
    assert late.status_code == 400 and late.json()["error"]["code"] == "verify_link_expired"
    assert (await anon.post("/api/auth/verify", json={"token": "x" * 20})).status_code == 400


async def test_repeated_tries_are_refused(
    api: httpx.AsyncClient, anon: httpx.AsyncClient, sql: psycopg.Connection[Any],
    mails: list[Mail], monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    await api.post("/api/auth/sign-out")
    monkeypatch.setattr(get_settings(), "jap_auth_tries_per_hour_email", 2)
    codes = [(await anon.post("/api/auth/sign-up", json=NEW)).status_code for _ in range(4)]
    assert codes == [201, 201, 429, 429]  # per email
    assert sql.execute("SELECT count(*) FROM app_user").fetchone() == (2,)

    monkeypatch.setattr(get_settings(), "jap_auth_tries_per_hour_ip", 4)
    codes = []
    for i in range(3):  # one address, a new email each time: two more, then the address is out
        body = {"email": f"someone{i}@example.com", "password": PASSWORD, **TICK}
        codes.append((await anon.post("/api/auth/sign-up", json=body)).status_code)
    assert codes == [429, 429, 429]  # the four tries above already used the address's hour
    assert sql.execute("SELECT count(*) FROM app_user").fetchone() == (2,)
    blocked = await anon.post("/api/auth/sign-up", json=NEW)
    assert blocked.json()["error"]["code"] == "too_many_tries"
    # Forgot password and "send again" count separately, each with its own limit.
    forgot = [
        (await anon.post("/api/auth/forgot", json={"email": NEW["email"]})).status_code
        for _ in range(3)
    ]
    assert forgot == [202, 202, 429]
    keys = sql.execute("SELECT key FROM rate_limit").fetchall()
    assert keys and not any("example" in k[0] or "test" in k[0].split(":")[-1] for k in keys)


async def test_the_address_comes_from_the_trusted_header_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.requests import Request

    from auth.limits import client_ip

    def request(headers: dict[str, str]) -> Request:
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request({"type": "http", "headers": raw, "client": ("10.0.0.9", 1234)})

    spoofed = request({"X-Forwarded-For": "1.2.3.4", "CF-Connecting-IP": "5.6.7.8"})
    assert client_ip(spoofed) == "10.0.0.9"  # a header is whatever the caller typed
    monkeypatch.setattr(get_settings(), "jap_client_ip_header", "CF-Connecting-IP")
    assert client_ip(spoofed) == "5.6.7.8"
    assert client_ip(request({})) == "10.0.0.9"


async def test_a_human_is_asked_for_when_turnstile_is_on(
    api: httpx.AsyncClient, anon: httpx.AsyncClient, mails: list[Mail],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    await api.post("/api/auth/sign-out")
    monkeypatch.setattr(get_settings(), "turnstile_site_key", "site")
    monkeypatch.setattr(get_settings(), "turnstile_secret_key", "secret")
    assert (await anon.get("/api/auth/state")).json()["turnstile_site_key"] == "site"
    assert await turnstile.passed(None, "1.2.3.4") is False  # no token: no call, no pass
    none = await anon.post("/api/auth/sign-up", json=NEW)
    assert none.status_code == 403 and none.json()["error"]["code"] == "not_human"
    assert (await anon.post("/api/auth/forgot", json={"email": NEW["email"]})).status_code == 403

    asked: list[tuple[str | None, str]] = []

    async def fake_passed(token: str | None, ip: str) -> bool:
        asked.append((token, ip))
        return token == "good"

    monkeypatch.setattr(turnstile, "passed", fake_passed)
    bad = await anon.post("/api/auth/sign-up", json={**NEW, "turnstile": "bad"})
    assert bad.status_code == 403 and mails == []
    good = await anon.post("/api/auth/sign-up", json={**NEW, "turnstile": "good"})
    assert good.status_code == 201 and len(mails) == 1
    assert [t for t, _ in asked] == ["bad", "good"]


async def test_admin_pauses_sign_up_and_turns_an_account_off(
    api: httpx.AsyncClient, other: httpx.AsyncClient, other_id: uuid.UUID, mails: list[Mail]
) -> None:
    from tests.conftest import _client

    # Only the admin reaches the admin's routes; to anyone else they do not exist.
    for path in ("/api/admin/accounts", "/api/ops", "/api/ops/status", "/api/duplicates"):
        assert (await other.get(path)).status_code == 404, path
        assert (await api.get(path)).status_code == 200, path
    assert (await other.put("/api/admin/sign-up", json={"paused": True})).status_code == 404
    assert (await other.post(f"/api/admin/accounts/{other_id}/on")).status_code == 404
    assert (await other.post("/api/ops/backup")).status_code == 404

    listed = (await api.get("/api/admin/accounts")).json()
    assert [(a["email"], a["is_admin"], a["state"]) for a in listed["accounts"]] == [
        (OWNER_EMAIL, True, "active"),
        (OTHER_EMAIL, False, "active"),
    ]
    assert listed["sign_up_paused"] is False and listed["accounts"][1]["last_sign_in_at"]
    # No AI on JAP's key until the admin grants some (launch decision D1).
    assert listed["accounts"][1]["spend"] == {"month_usd": 0.0, "cap_usd": 0.0, "reached": True}
    assert listed["accounts"][1]["ai_source"] == "none"
    free = {
        "name": "Free",
        "code": "free",
        "boards": 1,
        "applications_per_day": 1,
        "ai_usd": 0.0,
        "custom": False,
    }
    assert listed["accounts"][1]["plan"] == free
    assert listed["accounts"][0]["plan"]["name"] == "Admin"
    assert listed["accounts"][0]["plan"]["boards"] is None  # the admin is not held to the plan

    paused = await api.put("/api/admin/sign-up", json={"paused": True})
    assert paused.json()["sign_up_paused"] is True
    async with _client() as new:
        assert (await new.get("/api/auth/state")).json()["sign_up_open"] is False
        refused = await new.post("/api/auth/sign-up", json=NEW)
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "sign_up_closed"
        assert mails == []
        await api.put("/api/admin/sign-up", json={"paused": False})
        assert (await new.post("/api/auth/sign-up", json=NEW)).status_code == 201

    # Off: signed out everywhere, can't sign in, and its data is kept for when it is on again.
    await other.put("/api/profile", json={"full_name": "Other Person"})
    off = await api.post(f"/api/admin/accounts/{other_id}/off")
    assert [a["state"] for a in off.json()["accounts"]] == ["active", "off", "unverified"]
    assert (await other.get("/api/profile")).status_code == 401
    creds = {"email": OTHER_EMAIL, "password": OTHER_PASSWORD}
    denied = await other.post("/api/auth/sign-in", json=creds)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "account_off"
    me = (await api.get("/api/auth/me")).json()["id"]
    assert (await api.post(f"/api/admin/accounts/{me}/off")).status_code == 409  # never the admin
    await api.post(f"/api/admin/accounts/{other_id}/on")
    assert (await other.post("/api/auth/sign-in", json=creds)).status_code == 200
    assert (await other.get("/api/profile")).json()["full_name"] == "Other Person"


async def test_a_turned_off_account_stops_searching(
    api: httpx.AsyncClient, other: httpx.AsyncClient, other_id: uuid.UUID,
    sql: psycopg.Connection[Any],
) -> None:  # fmt: skip
    from database import tenant
    from database.session import get_sessionmaker
    from functions.triggers import due_users
    from pipeline.runs import start_run

    run = (await other.post("/api/runs", json={})).json()
    sql.execute("UPDATE profile SET daily_run = true, daily_run_at = (now() AT TIME ZONE"
                " 'Asia/Kolkata')::time WHERE user_id = %s", (other_id,))  # fmt: skip
    await api.post(f"/api/admin/accounts/{other_id}/off")
    assert await due_users() == []  # no Daily run for an account that is off
    with tenant.as_system():
        async with get_sessionmaker()() as session:
            assert await start_run(session, uuid.UUID(run["id"])) == []
    assert sql.execute("SELECT status, error FROM run").fetchall() == [
        ("failed", "This account is turned off")
    ]


async def test_first_sign_up_takes_over_data_from_before_any_account(
    anon: httpx.AsyncClient, db: str
) -> None:
    """0011 gives data that predates sign-in to a placeholder owner; the first account
    becomes that owner, so the data is the admin's (S1)."""
    with psycopg.connect(_libpq(db), autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO app_user (email, password_hash, is_admin, email_verified_at)"
            " VALUES ('unclaimed@jap.invalid', '', true, now()) RETURNING id"
        ).fetchone()
        assert row is not None
        conn.execute(
            "INSERT INTO profile (user_id, full_name) VALUES (%s, 'From Before')", (row[0],)
        )
    state = (await anon.get("/api/auth/state")).json()
    assert state["has_accounts"] is False  # the placeholder is not an account
    nobody = await anon.post(
        "/api/auth/sign-in", json={"email": "unclaimed@jap.invalid", "password": ""}
    )
    assert nobody.status_code in (401, 422)
    first = await anon.post(
        "/api/auth/sign-up", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD, **TICK}
    )
    assert first.status_code == 201 and first.json()["user"]["is_admin"] is True
    assert first.json()["user"]["id"] == str(row[0])
    assert (await anon.get("/api/profile")).json()["full_name"] == "From Before"


async def test_while_paused_only_an_invited_address_may_sign_up(
    api: httpx.AsyncClient, mails: list[Mail]
) -> None:
    """The soft launch's way in (launch decision D4): sign-up stays paused, and the admin
    names who may come in. An invite admits one account and then leaves the list."""
    from tests.conftest import _client

    await api.put("/api/admin/sign-up", json={"paused": True})
    async with _client() as new:
        state = (await new.get("/api/auth/state")).json()
        assert state["sign_up_open"] is False and state["sign_up_by_invite"] is False
        await api.post("/api/admin/invites", json={"email": "NEW@example.com"})
        state = (await new.get("/api/auth/state")).json()
        assert state["sign_up_open"] is False and state["sign_up_by_invite"] is True

        stranger = await new.post("/api/auth/sign-up", json={**NEW, "email": "x@example.com"})
        assert stranger.status_code == 403
        assert stranger.json()["error"]["code"] == "sign_up_closed"
        invited = await new.post("/api/auth/sign-up", json=NEW)  # any case of the address
        assert invited.status_code == 201 and invited.json()["verify_email"] is True
        assert [m.to for m in mails] == ["new@example.com"]

    listed = (await api.get("/api/admin/accounts")).json()
    assert listed["invites"] == [] and listed["sign_up_paused"] is True
    assert "new@example.com" in [a["email"] for a in listed["accounts"]]
