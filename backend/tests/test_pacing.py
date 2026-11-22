"""Applying side by side, and paced across every account (IDEA-15, IDEA-17).

Each account's own cap and 30-second gap are in test_apply.py. These are the limits that
span accounts, which are the ones a job site actually feels.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import inngest
import psycopg
import pytest
from sqlalchemy import text

from config import get_settings
from database.session import get_sessionmaker
from functions.apply_listing import apply_listing
from observability.capacity import capacity
from pipeline.apply import flow
from pipeline.apply.form import launch_args
from tests.test_apply import _listing, _profile, _resume


@pytest.fixture
def setup(db: str, sql: psycopg.Connection[Any], tmp_path: Path) -> dict[str, Any]:
    _profile(sql)
    return {"resume": _resume(sql, tmp_path), "listing": _listing(sql), "data": tmp_path}


def _applied(
    sql: psycopg.Connection[Any], at: datetime, n: int, user_id: uuid.UUID | None = None
) -> None:
    """An application already sent, on a job of its own: one Applied per account per
    listing is a rule of its own (one_applied_per_listing) and not what these test."""
    listing = _listing(sql, title=f"Backend Engineer {n}", ext=f"p{n}", company=f"Co{n}")
    sql.execute(
        "INSERT INTO application (user_id, listing_id, posting_id, resume_version_id, mode,"
        " status, submitted_at) SELECT COALESCE(%s, current_setting('app.user_id')::uuid),"
        " %s, p.id, r.id, 'review', 'applied', %s"
        " FROM posting p, resume_version r WHERE p.listing_id = %s LIMIT 1",
        (user_id, listing, at, listing),
    )


# --- IDEA-17: pacing across accounts ------------------------------------------------------


async def test_a_gap_is_kept_between_two_applications_to_one_site(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fifty accounts each politely spaced still arrive as fifty a minute at one site."""
    now = datetime.now(UTC)
    settings = get_settings()
    monkeypatch.setattr(settings, "jap_apply_ats_gap_seconds", 300)
    monkeypatch.setattr(settings, "jap_apply_gap_seconds", 0)
    _applied(sql, now - timedelta(seconds=60), 1)
    async with get_sessionmaker()() as session:
        # Another account's application to the same site still holds this one back.
        until = await flow.wait_before_submit(session, now, "greenhouse")
    assert until is not None and abs((until - now).total_seconds() - 240) < 2
    async with get_sessionmaker()() as session:
        other_site = await flow.wait_before_submit(session, now, "lever")
    assert other_site is None  # a different site is not held back by this one


async def test_the_service_stops_at_its_own_daily_ceiling(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under every account's own cap there is a ceiling for the whole service."""
    now = datetime.now(UTC)
    settings = get_settings()
    monkeypatch.setattr(settings, "jap_apply_gap_seconds", 0)
    monkeypatch.setattr(settings, "jap_apply_ats_gap_seconds", 0)
    monkeypatch.setattr(settings, "jap_daily_applications_total", 2)
    for i in range(2):
        _applied(sql, now - timedelta(seconds=10 * (i + 1)), i)
    async with get_sessionmaker()() as session:
        until = await flow.wait_before_submit(session, now, "greenhouse")
    assert until is not None and until > now  # tomorrow, not in a minute
    monkeypatch.setattr(settings, "jap_daily_applications_total", 50)
    async with get_sessionmaker()() as session:
        assert await flow.wait_before_submit(session, now, "greenhouse") is None


async def test_a_ceiling_of_zero_is_off(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    settings = get_settings()
    monkeypatch.setattr(settings, "jap_apply_gap_seconds", 0)
    monkeypatch.setattr(settings, "jap_apply_ats_gap_seconds", 0)
    monkeypatch.setattr(settings, "jap_daily_applications_total", 0)
    for i in range(3):
        _applied(sql, now - timedelta(seconds=10 * (i + 1)), i)
    async with get_sessionmaker()() as session:
        assert await flow.wait_before_submit(session, now, "greenhouse") is None


async def test_the_service_wide_counts_do_not_leak_other_peoples_rows(
    setup: dict[str, Any], sql: psycopg.Connection[Any], other_id: uuid.UUID
) -> None:
    """jap_applied_today answers with a number, never a row, so one account can ask how
    busy the service is without being able to read anybody else's applications."""
    _applied(sql, datetime.now(UTC) - timedelta(seconds=30), 1, user_id=other_id)
    async with get_sessionmaker()() as session:
        mine = await flow.applied_today(session, datetime.now(UTC))
        everyone = await session.scalar(text("SELECT jap_applied_today(now() - interval '1 day')"))
        rows = await session.scalar(text("SELECT count(*) FROM application"))
    # None of their rows are visible, and the count still says the service sent one.
    assert mine == 0 and int(rows or 0) == 0 and int(everyone or 0) == 1


async def test_a_paused_account_sends_nothing(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-user switch beside the global one: it waits, it does not fail."""
    from tests.test_apply import FORM, _fake_form, _prepare

    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(setup["listing"])
    sql.execute("UPDATE application SET status = 'applying' WHERE id = %s", (app_id,))
    sql.execute("UPDATE profile SET submit_paused = true")
    async with get_sessionmaker()() as session:
        result = await flow.submit(session, app_id)
    assert result.get("paused") is True and "wait_until" in result
    assert sql.execute("SELECT status FROM application").fetchone() == ("applying",)


# --- IDEA-15: side by side ----------------------------------------------------------------


def test_applying_is_one_browser_per_user_under_a_service_ceiling() -> None:
    """One per account so nobody has two of their own open, and a ceiling across the
    service because each open browser holds a worker."""
    # _opts is where the Inngest SDK keeps what the decorator was given; there is no
    # public reader for it, and the limits are worth pinning down.
    limits = {(c.key, c.limit) for c in apply_listing._opts.concurrency or []}
    assert ("event.data.user_id", 1) in limits
    assert (None, get_settings().jap_apply_browsers) in limits
    assert isinstance(apply_listing, inngest.Function)


# --- IDEA-18: the seam for an outbound address --------------------------------------------


def test_the_browser_leaves_from_the_server_until_a_proxy_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "jap_apply_proxy", "")
    assert launch_args() == {}
    monkeypatch.setattr(settings, "jap_apply_proxy", " http://user:pw@proxy.example:8080 ")
    assert launch_args() == {"proxy": {"server": "http://user:pw@proxy.example:8080"}}


# --- IDEA-17: what each site did with it ---------------------------------------------------


async def test_each_sites_answers_are_counted(db: str, sql: psycopg.Connection[Any]) -> None:
    import json

    for ats, confirmed, rejected, needs_code in (
        ("greenhouse", True, False, False),
        ("greenhouse", False, True, True),
        ("lever", True, False, False),
    ):
        sql.execute(
            "INSERT INTO log_event (level, event, message, logger, fields)"
            " VALUES ('info', 'apply.outcome', 'm', 'jap', %s)",
            (
                json.dumps(
                    {
                        "ats": ats,
                        "confirmed": confirmed,
                        "rejected": rejected,
                        "needs_code": needs_code,
                    }
                ),
            ),
        )
    async with get_sessionmaker()() as session:
        rows = {s.ats: s for s in (await capacity(session)).sites}
    assert rows["greenhouse"].submissions == 2
    assert rows["greenhouse"].confirmed == 1 and rows["greenhouse"].rejected == 1
    assert rows["greenhouse"].needs_code == 1
    assert rows["lever"].submissions == 1 and rows["lever"].rejected == 0
