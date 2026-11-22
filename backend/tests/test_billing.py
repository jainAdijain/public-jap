"""Paid plans (IDEA-21): what a plan allows, and what a webhook may do.

The two halves are tested apart on purpose. limits_for() is where money turns into what an
account may use, and it has to keep working for the accounts that never pay. The webhook is
the one route a stranger can reach, so most of this file is about what it refuses.

Razorpay is never called: Razorpay._post is replaced, as the OpenRouter client is in the
other tests. No key, no network and no sandbox account is needed to run any of this.
"""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest

from billing import razorpay as razorpay_module
from billing.providers import PROVIDERS
from config import get_settings
from database import tenant
from database.models import Subscription
from database.session import get_sessionmaker
from pipeline.limits import limits_for, plan_of
from tests.conftest import OTHER_EMAIL, OTHER_PASSWORD

SECRET = "a webhook secret for the tests"
REF = "sub_test0000000001"


@pytest.fixture
def selling(db: str, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A stack that is selling, with Razorpay answering from memory. Gives back the calls
    made to it, so a test can say what was asked of the provider and what never was."""
    settings = get_settings()
    monkeypatch.setattr(settings, "jap_billing_enabled", True)
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_key")
    monkeypatch.setattr(settings, "razorpay_key_secret", "rzp_test_secret")
    monkeypatch.setattr(settings, "razorpay_plan_id_plus", "plan_test_plus")
    monkeypatch.setattr(settings, "razorpay_webhook_secret", SECRET)
    calls: list[dict[str, Any]] = []

    async def fake_post(self: Any, path: str, body: dict[str, Any]) -> dict[str, Any]:
        calls.append({"path": path, "body": body})
        if path.endswith("/cancel"):
            return {"id": REF, "status": "cancelled"}
        return {"id": REF, "short_url": "https://rzp.io/i/test", "status": "created"}

    monkeypatch.setattr(razorpay_module.Razorpay, "_post", fake_post)
    return calls


def hook(event: str, *, at: datetime, ref: str = REF, end: datetime | None = None) -> bytes:
    """A Razorpay webhook body, as bytes, because the signature is over the bytes."""
    sub: dict[str, Any] = {"id": ref, "status": "active"}
    if end is not None:
        sub["current_end"] = int(end.timestamp())
    return json.dumps(
        {
            "event": event,
            "created_at": int(at.timestamp()),
            "payload": {"subscription": {"entity": sub}},
        }
    ).encode()


def signed(raw: bytes, event_id: str, secret: str = SECRET) -> dict[str, str]:
    return {
        "x-razorpay-signature": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
        "x-razorpay-event-id": event_id,
        "content-type": "application/json",
    }


async def post_hook(anon: httpx.AsyncClient, raw: bytes, headers: dict[str, str]) -> httpx.Response:
    return await anon.post("/api/webhooks/razorpay", content=raw, headers=headers)


def add_subscription(
    sql: psycopg.Connection[Any],
    user_id: uuid.UUID,
    *,
    status: str = "active",
    plan: str = "plus",
    end: datetime | None = None,
    ref: str = REF,
    state_at: datetime | None = None,
) -> None:
    sql.execute(
        "INSERT INTO subscription"
        " (user_id, provider, provider_ref, plan_code, status, current_period_end, state_at)"
        " VALUES (%s, 'razorpay', %s, %s, %s, %s, %s)",
        (user_id, ref, plan, status, end, state_at),
    )
    sql.commit()


async def limits_of(user_id: uuid.UUID) -> Any:
    async with get_sessionmaker()() as session:
        session.info[tenant.INFO_KEY] = tenant.user(user_id)
        return await limits_for(session, user_id)


# --- what a plan allows -------------------------------------------------------------------------


async def test_an_account_with_no_subscription_is_on_free(owner: uuid.UUID) -> None:
    """The case that must never break: nearly every account, and every account there was
    before any of this existed."""
    limits = await limits_of(owner)
    settings = get_settings()
    assert limits.plan_code == "admin"  # the owner fixture is the admin, held to no plan
    assert limits.applications_per_day == 100
    assert settings.jap_free_boards == 1  # and the free plan is still what .env says


async def test_a_paid_subscription_raises_the_limits(
    db: str, other_id: uuid.UUID, sql: psycopg.Connection[Any]
) -> None:
    before = await limits_of(other_id)
    assert before.plan_code == "free"
    assert before.boards == get_settings().jap_free_boards
    add_subscription(sql, other_id, end=datetime.now(UTC) + timedelta(days=20))
    after = await limits_of(other_id)
    assert after.plan_code == "plus"
    assert after.boards == get_settings().jap_plus_boards
    assert after.applications_per_day == get_settings().jap_plus_daily_applications


async def test_a_failed_renewal_keeps_the_plan_to_the_end_of_the_period(
    db: str, other_id: uuid.UUID, sql: psycopg.Connection[Any]
) -> None:
    """past_due is not the same as unpaid: the provider is still retrying, and the account
    keeps what it bought until the period it paid for runs out."""
    add_subscription(sql, other_id, status="past_due", end=datetime.now(UTC) + timedelta(days=5))
    assert (await limits_of(other_id)).plan_code == "plus"


async def test_a_plan_runs_out_after_the_period_and_the_grace(
    db: str, other_id: uuid.UUID, sql: psycopg.Connection[Any]
) -> None:
    gone = datetime.now(UTC) - timedelta(days=get_settings().jap_billing_grace_days + 1)
    add_subscription(sql, other_id, status="past_due", end=gone)
    assert (await limits_of(other_id)).plan_code == "free"


async def test_a_checkout_nobody_paid_for_grants_nothing(
    db: str, other_id: uuid.UUID, sql: psycopg.Connection[Any]
) -> None:
    add_subscription(sql, other_id, status="pending")
    assert (await limits_of(other_id)).plan_code == "free"


async def test_the_admins_numbers_for_one_account_beat_the_plan(
    db: str, other_id: uuid.UUID, sql: psycopg.Connection[Any]
) -> None:
    add_subscription(sql, other_id, end=datetime.now(UTC) + timedelta(days=20))
    sql.execute("UPDATE app_user SET limit_boards = 2 WHERE id = %s", (other_id,))
    sql.commit()
    limits = await limits_of(other_id)
    assert limits.plan_code == "plus"  # still on the plan they are paying for
    assert limits.boards == 2  # and held to what the admin set for them
    assert limits.custom is True


async def test_a_user_can_still_hold_themselves_below_their_plan(
    db: str, other_id: uuid.UUID, sql: psycopg.Connection[Any]
) -> None:
    add_subscription(sql, other_id, end=datetime.now(UTC) + timedelta(days=20))
    # The profile row is made lazily (tests/conftest.py, add_account), so this writes it.
    sql.execute(
        "INSERT INTO profile (user_id, daily_cap) VALUES (%s, 3)"
        " ON CONFLICT (user_id) DO UPDATE SET daily_cap = 3",
        (other_id,),
    )
    sql.commit()
    assert (await limits_of(other_id)).applications_per_day == 3


async def test_a_plan_that_no_longer_exists_falls_back_to_free(
    db: str, other_id: uuid.UUID, sql: psycopg.Connection[Any]
) -> None:
    """A plan withdrawn from sale must not leave its subscribers with no limits at all."""
    add_subscription(sql, other_id, plan="gone", end=datetime.now(UTC) + timedelta(days=20))
    async with get_sessionmaker()() as session:
        session.info[tenant.INFO_KEY] = tenant.user(other_id)
        assert (await plan_of(session, other_id)).code == "free"


# --- the webhook, which is the one route a stranger can reach ------------------------------------


async def test_an_unsigned_webhook_is_not_found_and_changes_nothing(
    selling: list[dict[str, Any]],
    anon: httpx.AsyncClient,
    other_id: uuid.UUID,
    sql: psycopg.Connection[Any],
) -> None:
    add_subscription(sql, other_id, status="pending")
    raw = hook("subscription.activated", at=datetime.now(UTC))
    resp = await post_hook(anon, raw, {"x-razorpay-event-id": "evt_1"})
    assert resp.status_code == 404
    bad = signed(raw, "evt_2", secret="not the secret")
    assert (await post_hook(anon, raw, bad)).status_code == 404
    assert sql.execute("SELECT count(*) FROM billing_event").fetchone() == (0,)
    assert sql.execute("SELECT status FROM subscription").fetchone() == ("pending",)


async def test_a_signed_webhook_moves_the_subscription(
    selling: list[dict[str, Any]],
    anon: httpx.AsyncClient,
    other_id: uuid.UUID,
    sql: psycopg.Connection[Any],
) -> None:
    add_subscription(sql, other_id, status="pending")
    end = datetime.now(UTC) + timedelta(days=30)
    raw = hook("subscription.charged", at=datetime.now(UTC), end=end)
    assert (await post_hook(anon, raw, signed(raw, "evt_1"))).status_code == 200
    row = sql.execute("SELECT status, current_period_end FROM subscription").fetchone()
    assert row is not None and row[0] == "active"
    assert row[1] is not None
    assert (await limits_of(other_id)).plan_code == "plus"


async def test_the_same_delivery_twice_is_handled_once(
    selling: list[dict[str, Any]],
    anon: httpx.AsyncClient,
    other_id: uuid.UUID,
    sql: psycopg.Connection[Any],
) -> None:
    """Providers retry, and a retry must not be a second event."""
    add_subscription(sql, other_id, status="pending")
    raw = hook("subscription.charged", at=datetime.now(UTC))
    headers = signed(raw, "evt_same")
    assert (await post_hook(anon, raw, headers)).status_code == 200
    assert (await post_hook(anon, raw, headers)).status_code == 200
    assert sql.execute("SELECT count(*) FROM billing_event").fetchone() == (1,)
    handled = sql.execute("SELECT handled FROM billing_event").fetchone()
    assert handled is not None and "active" in (handled[0] or "")


async def test_a_webhook_that_arrives_late_does_not_undo_a_newer_one(
    selling: list[dict[str, Any]],
    anon: httpx.AsyncClient,
    other_id: uuid.UUID,
    sql: psycopg.Connection[Any],
) -> None:
    """Deliveries overtake each other. A "a charge failed" sent before the charge that
    succeeded must not put a paid-up account back into arrears."""
    add_subscription(sql, other_id, status="pending")
    now = datetime.now(UTC)
    newer = hook("subscription.charged", at=now)
    assert (await post_hook(anon, newer, signed(newer, "evt_new"))).status_code == 200
    older = hook("subscription.pending", at=now - timedelta(hours=2))
    assert (await post_hook(anon, older, signed(older, "evt_old"))).status_code == 200
    assert sql.execute("SELECT status FROM subscription").fetchone() == ("active",)
    rows = sql.execute("SELECT handled FROM billing_event WHERE event_id = 'evt_old'").fetchone()
    assert rows is not None and rows[0] == "older than the state held"


async def test_a_webhook_for_a_subscription_nobody_here_has_is_ignored(
    selling: list[dict[str, Any]], anon: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    raw = hook("subscription.charged", at=datetime.now(UTC), ref="sub_someone_elses")
    assert (await post_hook(anon, raw, signed(raw, "evt_x"))).status_code == 200
    handled = sql.execute("SELECT handled FROM billing_event").fetchone()
    assert handled is not None and handled[0] == "no such subscription here"


async def test_the_webhook_is_off_when_no_provider_is_configured(
    db: str, anon: httpx.AsyncClient
) -> None:
    """With no keys there is nothing to verify a signature against, so the route is not
    there at all rather than open."""
    raw = hook("subscription.charged", at=datetime.now(UTC))
    assert (await post_hook(anon, raw, signed(raw, "evt_1"))).status_code == 404


# --- the routes an account uses ------------------------------------------------------------------


async def test_the_plan_screen_reads_without_a_provider(api: httpx.AsyncClient) -> None:
    body = (await api.get("/api/billing")).json()
    assert body["selling"] is False and body["providers"] == []
    assert [p["code"] for p in body["plans"]] == ["free", "plus"]
    assert body["subscription"] is None


async def test_checkout_sends_the_browser_to_the_providers_page(
    selling: list[dict[str, Any]], api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    resp = await api.post(
        "/api/billing/checkout", json={"plan_code": "plus", "provider": "razorpay"}
    )
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://rzp.io/i/test"
    row = sql.execute("SELECT status, plan_code, provider_ref FROM subscription").fetchone()
    assert row == ("pending", "plus", REF)  # pending: nothing is granted until it is paid
    assert selling[0]["body"]["plan_id"] == "plan_test_plus"
    # Nothing about the person goes to the provider; they type their own details there.
    assert set(selling[0]["body"]["notes"]) == {"user_id"}


async def test_checkout_is_refused_while_the_service_is_not_selling(
    db: str, api: httpx.AsyncClient
) -> None:
    resp = await api.post(
        "/api/billing/checkout", json={"plan_code": "plus", "provider": "razorpay"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "not_selling"


async def test_a_second_checkout_is_refused_while_one_is_paid_for(
    selling: list[dict[str, Any]],
    api: httpx.AsyncClient,
    owner: uuid.UUID,
    sql: psycopg.Connection[Any],
) -> None:
    add_subscription(sql, owner, end=datetime.now(UTC) + timedelta(days=20))
    resp = await api.post(
        "/api/billing/checkout", json={"plan_code": "plus", "provider": "razorpay"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "already_subscribed"


async def test_an_abandoned_checkout_does_not_block_another(
    selling: list[dict[str, Any]],
    api: httpx.AsyncClient,
    owner: uuid.UUID,
    sql: psycopg.Connection[Any],
) -> None:
    """A pending row grants nothing, so it must not stand between somebody and paying."""
    add_subscription(sql, owner, status="pending", ref="sub_abandoned")
    resp = await api.post(
        "/api/billing/checkout", json={"plan_code": "plus", "provider": "razorpay"}
    )
    assert resp.status_code == 200
    states = sql.execute("SELECT status FROM subscription ORDER BY created_at").fetchall()
    assert sorted(s[0] for s in states) == ["expired", "pending"]


async def test_cancelling_keeps_the_plan_to_the_end_of_the_period(
    selling: list[dict[str, Any]],
    api: httpx.AsyncClient,
    owner: uuid.UUID,
    sql: psycopg.Connection[Any],
) -> None:
    add_subscription(sql, owner, end=datetime.now(UTC) + timedelta(days=20))
    body = (await api.post("/api/billing/cancel", json={})).json()
    assert body["subscription"]["cancel_at_period_end"] is True
    assert body["subscription"]["granting"] is True  # still theirs until the period ends
    assert selling[-1]["path"].endswith(f"/v1/subscriptions/{REF}/cancel")
    assert selling[-1]["body"] == {"cancel_at_cycle_end": 1}


async def test_cancelling_nothing_says_so(
    selling: list[dict[str, Any]], api: httpx.AsyncClient
) -> None:
    resp = await api.post("/api/billing/cancel", json={})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_plan"


async def test_one_account_never_sees_or_cancels_anothers_plan(
    selling: list[dict[str, Any]],
    other: httpx.AsyncClient,
    owner: uuid.UUID,
    sql: psycopg.Connection[Any],
) -> None:
    """The plan is private data like everything else (PRD S7): B gets their own answer,
    never A's, and cannot stop A's plan."""
    add_subscription(sql, owner, end=datetime.now(UTC) + timedelta(days=20))
    body = (await other.get("/api/billing")).json()
    assert body["subscription"] is None
    assert (await other.post("/api/billing/cancel", json={})).status_code == 409
    assert sql.execute("SELECT cancel_at_period_end FROM subscription").fetchone() == (False,)


async def test_a_subscription_row_is_hidden_from_every_other_tenant(
    db: str, owner: uuid.UUID, other_id: uuid.UUID, sql: psycopg.Connection[Any]
) -> None:
    """Postgres refuses too, not only the route (migration 0018_billing)."""
    add_subscription(sql, owner, end=datetime.now(UTC) + timedelta(days=20))

    async def seen(scope: tenant.Tenant) -> int:
        async with get_sessionmaker()() as session:
            session.info[tenant.INFO_KEY] = scope
            return len(list(await session.scalars(Subscription.__table__.select())))

    assert await seen(tenant.user(owner)) == 1
    assert await seen(tenant.user(other_id)) == 0
    assert await seen(tenant.NOBODY) == 0
    assert await seen(tenant.SYSTEM) == 1  # the webhook, which has no user to be


async def test_deleting_an_account_stops_the_money_first(
    selling: list[dict[str, Any]],
    other: httpx.AsyncClient,
    other_id: uuid.UUID,
    sql: psycopg.Connection[Any],
) -> None:
    """A deleted account must not leave a mandate behind that goes on charging someone who
    no longer has an account here (PRD S15)."""
    add_subscription(sql, other_id, end=datetime.now(UTC) + timedelta(days=20))
    resp = await other.post(
        "/api/account/delete", json={"password": OTHER_PASSWORD, "confirm_email": OTHER_EMAIL}
    )
    assert resp.status_code == 200, resp.text
    assert any(c["path"].endswith("/cancel") for c in selling)
    assert sql.execute("SELECT count(*) FROM subscription").fetchone() == (0,)


def test_every_provider_is_named_the_same_as_its_key() -> None:
    for name, provider in PROVIDERS.items():
        assert provider.name == name
