"""Sign in with Google (PRD S16).

Google is stood in for the way the job boards are (tests/boards.py): auth.oidc.client is
replaced with one talking to a MockTransport, so make test reaches no network and needs no
OAuth client. The ID token is built here as a real one is shaped, because JAP reads its
claims rather than its signature (auth/oidc.py says why).
"""

import base64
import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest

from auth import oidc
from auth.consent import TERMS_VERSION
from config import get_settings
from tests.conftest import OTHER_EMAIL, OWNER_EMAIL, OWNER_PASSWORD, _client

ISSUER = "https://accounts.example.test"
CLIENT_ID = "jap-test-client"
SUBJECT = "google-subject-123"


def _jwt(**claims: Any) -> str:
    """A JWT shaped like Google's, with a signature nobody checks (auth/oidc.py)."""
    body = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "exp": int(time.time()) + 300,
        "sub": SUBJECT,
        "email": OTHER_EMAIL,
        "email_verified": True,
        **claims,
    }

    def part(raw: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(raw).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part(body)}.not-checked"


@pytest.fixture
def google(db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Google turned on and stood in for. token["claims"] is what the next exchange
    returns; token["status"] fails it."""
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(settings, "google_redirect_url", "http://localhost/api/auth/google/cb")
    monkeypatch.setattr(settings, "google_issuer_url", ISSUER)
    oidc.forget_discovery()  # the endpoints are cached per process, and these are not real

    state: dict[str, Any] = {"claims": {}, "status": 200, "posts": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": f"{ISSUER}/o/oauth2/v2/auth",
                    "token_endpoint": f"{ISSUER}/token",
                },
            )
        state["posts"].append(dict(httpx.QueryParams(request.content.decode())))
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"error": "no"})
        return httpx.Response(200, json={"id_token": _jwt(**state["claims"])})

    def stub() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(oidc, "client", stub)
    yield state
    oidc.forget_discovery()


async def _dance(
    client: httpx.AsyncClient, google: dict[str, Any], **claims: Any
) -> httpx.Response:
    """Start, then come back with the code, as a browser does. Google echoes the nonce it
    was given back in the token, so the stub is told it only once the handshake has one."""
    start = await client.get("/api/auth/google/start", follow_redirects=False)
    assert start.status_code == 303, start.text
    assert start.headers["location"].startswith(f"{ISSUER}/o/oauth2/v2/auth?")
    sent = httpx.URL(start.headers["location"]).params
    assert sent["code_challenge_method"] == "S256" and sent["client_id"] == CLIENT_ID
    _, nonce, _verifier = client.cookies["jap_oauth"].split(".")
    assert sent["nonce"] == nonce
    google["claims"] = {"nonce": nonce, **claims}
    return await client.get(
        f"/api/auth/google/callback?code=abc&state={sent['state']}", follow_redirects=False
    )


def _outcome(resp: httpx.Response) -> str:
    assert resp.status_code == 303, resp.text
    return str(httpx.URL(resp.headers["location"]).params.get("google"))


async def test_google_is_not_there_until_it_is_configured(anon: httpx.AsyncClient) -> None:
    """Empty GOOGLE_ settings: no button, and the routes do not exist."""
    assert (await anon.get("/api/auth/state")).json()["google"] is False
    assert (await anon.get("/api/auth/google/start")).status_code == 404
    assert (await anon.get("/api/auth/google/callback?code=x&state=y")).status_code == 404


async def test_a_new_visitor_gets_an_account_and_must_still_agree(
    google: dict[str, Any], anon: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """The first Google sign-in makes the account. It has no password at all, and it meets
    the agreement screen before it can reach anything (S14)."""
    assert (await anon.get("/api/auth/state")).json()["google"] is True
    assert _outcome(await _dance(anon, google)) == "signed_in"
    assert google["posts"][0]["code_verifier"]  # PKCE, not just the code
    row = sql.execute(
        "SELECT password_hash, email_verified_at IS NOT NULL, terms_accepted_at"
        " FROM app_user WHERE email = %s",
        (OTHER_EMAIL,),
    ).fetchone()
    assert row == ("", True, None)  # no password, address taken as confirmed, not agreed
    me = await anon.get("/api/auth/me")
    assert me.json()["google_connected"] is True and me.json()["has_password"] is False
    assert me.json()["terms_accepted"] is False
    assert (await anon.get("/api/profile")).status_code == 403  # the gate holds
    await anon.post("/api/auth/terms", json={"terms_version": TERMS_VERSION})
    assert (await anon.get("/api/profile")).status_code == 200


async def test_coming_back_signs_in_to_the_same_account(
    google: dict[str, Any], anon: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    assert _outcome(await _dance(anon, google)) == "signed_in"
    first = (await anon.get("/api/auth/me")).json()["id"]
    await anon.post("/api/auth/sign-out")
    async with _client() as again:
        assert _outcome(await _dance(again, google)) == "signed_in"
        assert (await again.get("/api/auth/me")).json()["id"] == first
    # One account and one link, however many times they come back. (The owner account the
    # sql fixture makes is the other row in app_user.)
    theirs = sql.execute("SELECT count(*) FROM app_user WHERE email = %s", (OTHER_EMAIL,))
    assert theirs.fetchone() == (1,)
    assert sql.execute("SELECT count(*) FROM user_identity").fetchone() == (1,)


async def test_an_address_is_only_joined_up_when_google_vouches_for_it(
    google: dict[str, Any], api: httpx.AsyncClient, anon: httpx.AsyncClient,
    sql: psycopg.Connection[Any],
) -> None:  # fmt: skip
    """An unverified Google address must not reach an account that already exists: anyone
    can make a Google account claiming an address they do not have."""
    await api.post("/api/auth/sign-out")
    refused = await _dance(anon, google, email=OWNER_EMAIL, email_verified=False)
    assert _outcome(refused) == "email_unverified"
    assert sql.execute("SELECT count(*) FROM user_identity").fetchone() == (0,)
    # Verified, and it joins up with the password account instead of making a second one.
    assert _outcome(await _dance(anon, google, email=OWNER_EMAIL)) == "signed_in"
    assert sql.execute("SELECT count(*) FROM app_user").fetchone() == (1,)
    me = (await anon.get("/api/auth/me")).json()
    assert me["email"] == OWNER_EMAIL and me["google_connected"] and me["has_password"]


async def test_connect_and_disconnect_from_settings(
    google: dict[str, Any], api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """Signed in already: the callback links Google to that account rather than signing in
    as somebody. Disconnecting is allowed because there is still a password."""
    assert _outcome(await _dance(api, google, email="other.address@example.com")) == "connected"
    assert (await api.get("/api/auth/me")).json()["google_connected"] is True
    assert sql.execute("SELECT email FROM app_user").fetchone() == (OWNER_EMAIL,)  # unchanged
    off = await api.post("/api/auth/google/disconnect")
    assert off.status_code == 200 and off.json()["google_connected"] is False
    assert sql.execute("SELECT count(*) FROM user_identity").fetchone() == (0,)


async def test_the_last_way_in_cannot_be_disconnected(
    google: dict[str, Any], anon: httpx.AsyncClient
) -> None:
    assert _outcome(await _dance(anon, google)) == "signed_in"
    refused = await anon.post("/api/auth/google/disconnect")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "no_password"


async def test_a_google_only_account_can_still_sign_in_with_nothing_else(
    google: dict[str, Any], anon: httpx.AsyncClient
) -> None:
    """password_hash is empty, and an empty hash must answer exactly as a wrong password
    does rather than letting anybody in."""
    assert _outcome(await _dance(anon, google)) == "signed_in"
    await anon.post("/api/auth/sign-out")
    for password in ("", "anything at all", OWNER_PASSWORD):
        resp = await anon.post(
            "/api/auth/sign-in", json={"email": OTHER_EMAIL, "password": password}
        )
        assert resp.status_code in (401, 422), password


async def test_a_callback_that_did_not_start_here_is_refused(
    google: dict[str, Any], anon: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """No cookie, or a state that does not match it: nothing happens."""
    assert _outcome(await anon.get("/api/auth/google/callback?code=abc&state=guess")) == "expired"
    start = await anon.get("/api/auth/google/start", follow_redirects=False)
    assert start.status_code == 303
    wrong = await anon.get("/api/auth/google/callback?code=abc&state=not-the-state")
    assert _outcome(wrong) == "expired"
    assert sql.execute("SELECT count(*) FROM user_identity").fetchone() == (0,)


async def test_a_token_for_another_sign_in_or_another_client_is_refused(
    google: dict[str, Any], anon: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """The claims JAP does check, since it does not check the signature."""
    for claims in (
        {"nonce": "someone else's sign-in"},
        {"aud": "another-client"},
        {"iss": "https://evil.example"},
        {"exp": int(time.time()) - 60},
        {"sub": ""},
    ):
        assert _outcome(await _dance(anon, google, **claims)) == "failed", claims
    google["status"] = 400
    assert _outcome(await _dance(anon, google)) == "failed"
    assert sql.execute("SELECT count(*) FROM user_identity").fetchone() == (0,)


async def test_google_cannot_take_an_account_that_is_off_or_sign_up_while_paused(
    google: dict[str, Any], api: httpx.AsyncClient, other_id: uuid.UUID
) -> None:
    """A visitor, so a fresh client: the api fixture signs the owner in on the one `anon`
    returns, and a signed-in callback would take the Connect path instead."""
    await api.put("/api/admin/sign-up", json={"paused": True})
    async with _client() as visitor:
        shut = await _dance(visitor, google, email="stranger@example.com")
        assert _outcome(shut) == "sign_up_closed"
        await api.put("/api/admin/sign-up", json={"paused": False})
        # An account that is turned off stays out, however it signs in.
        await api.post(f"/api/admin/accounts/{other_id}/off")
        assert _outcome(await _dance(visitor, google, email=OTHER_EMAIL)) == "account_off"
        assert (await visitor.get("/api/auth/me")).status_code == 401


async def test_google_signs_up_an_invited_address_while_paused_once_it_vouches_for_it(
    google: dict[str, Any], api: httpx.AsyncClient
) -> None:
    """An invite is for an address (launch decision D4), so Google must say the address is
    really theirs before it lets anyone in; the invite is then used up."""
    await api.put("/api/admin/sign-up", json={"paused": True})
    await api.post("/api/admin/invites", json={"email": "friend@example.com"})
    async with _client() as visitor:
        shaky = await _dance(visitor, google, email="friend@example.com", email_verified=False)
        assert _outcome(shaky) == "sign_up_closed"
    async with _client() as visitor:
        sure = await _dance(visitor, google, email="friend@example.com", sub="friend-sub")
        assert _outcome(sure) != "sign_up_closed"
        assert (await visitor.get("/api/auth/me")).json()["email"] == "friend@example.com"
    assert (await api.get("/api/admin/accounts")).json()["invites"] == []


async def test_one_google_account_cannot_be_on_two_jap_accounts(
    google: dict[str, Any], api: httpx.AsyncClient, other: httpx.AsyncClient
) -> None:
    assert _outcome(await _dance(api, google, email="shared@example.com")) == "connected"
    assert _outcome(await _dance(other, google, email="shared@example.com")) == "google_taken"
    assert (await other.get("/api/auth/me")).json()["google_connected"] is False
