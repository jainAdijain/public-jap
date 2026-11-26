"""Shared fixtures. Database tests run against a separate <db>_test database.

The test database is created and migrated once per session from DATABASE_URL (the dev
database itself is never touched). Without a reachable Postgres those tests are skipped,
unless JAP_REQUIRE_DB=1 (CI), where they fail instead.

Private data needs a user (PRD S7). `owner` is the admin account: a test that asks for it
(or for `sql` or `api`, which do) runs as that user, the way a request or an Inngest call
for them would, so pipeline functions called straight from a test read and write the
owner's rows. `sql` is the table owner's connection with app.user_id set to the same
account, so a row inserted by hand without a user_id is the owner's too. `other` is a
second, ordinary account, for checking that one user never reaches another's data.
"""

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from config import get_settings
from database import session as db_session
from database import tenant

BACKEND = Path(__file__).resolve().parent.parent
TABLES = (
    "email_verification, rate_limit, app_setting, user_listing, board_follow, "
    "auth_session, password_reset, app_user, "
    "log_event, route_stat, alert_state, "
    "form_check, llm_call, possible_duplicate, dedup_override, skill_alias, company_alias, "
    # subscription would go with app_user through CASCADE; billing_event has no foreign key
    # to anything, so it is only ever emptied because it is named here.
    "subscription, billing_event, "
    "answer_bank, "
    "application, run, posting, listing, watched_board, company, resume_version, profile"
)


def _test_url() -> str:
    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture(scope="session", autouse=True)
def no_fresh_window() -> Iterator[None]:
    """The board freshness window off for the suite (T6). Tests fetch one board over and
    over on purpose — a second fetch that closes what left, a changed description — and
    the window would make every one of those a no-op. Set in the environment, not on the
    settings object, because the db fixture clears the settings cache for each test.
    tests/test_ingest.py turns it back on for the tests that are about it."""
    before = os.environ.get("JAP_BOARD_FRESH_MINUTES")
    os.environ["JAP_BOARD_FRESH_MINUTES"] = "0"
    get_settings.cache_clear()
    yield
    if before is None:
        os.environ.pop("JAP_BOARD_FRESH_MINUTES", None)
    else:
        os.environ["JAP_BOARD_FRESH_MINUTES"] = before
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    test_url = _test_url()
    name = make_url(test_url).database
    admin = make_url(test_url).set(database="postgres").render_as_string(hide_password=False)
    try:
        with psycopg.connect(_libpq(admin), autocommit=True, connect_timeout=3) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError as exc:
        if os.environ.get("JAP_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"Postgres not reachable: {exc}")
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_url
    get_settings.cache_clear()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "database" / "migrations"))
    command.upgrade(cfg, "head")
    yield test_url
    if old is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = old
    get_settings.cache_clear()


@pytest.fixture
def db(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A clean database and data/ folder for one test."""
    from observability import writer

    writer.flush()  # rows a previous test queued land before the truncate
    with psycopg.connect(_libpq(database_url), autocommit=True) as conn:
        conn.execute(f"TRUNCATE {TABLES} CASCADE")
    monkeypatch.setenv("JAP_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    yield database_url
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    get_settings.cache_clear()


OWNER_EMAIL = "owner@example.com"
OWNER_PASSWORD = "correct horse battery"
OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "another horse battery"


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cheap scrypt cost in tests; the real cost is checked in test_auth.py."""
    import auth.passwords

    monkeypatch.setattr(auth.passwords, "N", 2**10)


@pytest.fixture
def sending_on(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sending turned on for one test (H4). Only prod has JAP_SUBMIT_ENABLED on, and a
    worktree forces it off, so a test that drives Submit all the way through says so for
    itself instead of reading it off the stack it happens to run against. The browser is a
    stub in every one of them (flow.submit_form), so no job site is ever reached; the off
    case is test_apply.py::test_sending_off_never_opens_the_form.

    It takes db because that fixture clears the settings cache, which would drop this."""
    monkeypatch.setattr(get_settings(), "jap_submit_enabled", True)


def add_account(
    db: str, email: str, password: str, *, admin: bool = False, consented: bool = True
) -> uuid.UUID:
    """A confirmed account with an empty profile, written straight to the database.

    consented is the terms agreed (PRD S14), which is what an established account looks
    like; test_consent.py passes False for one that has agreed to nothing. The two apply
    opt-ins live on the profile, which is made lazily, so a test that applies asks for
    them with tests.mine.opt_in()."""
    from auth.consent import TERMS_VERSION
    from auth.passwords import _hash_sync

    with psycopg.connect(_libpq(db), autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO app_user (email, password_hash, is_admin, email_verified_at,"
            " terms_accepted_at, terms_version)"
            " VALUES (%s, %s, %s, now(), CASE WHEN %s THEN now() END, %s) RETURNING id",
            (email, _hash_sync(password), admin, consented, TERMS_VERSION if consented else None),
        ).fetchone()
        assert row is not None
        return uuid.UUID(str(row[0]))


@pytest.fixture
def owner(db: str, fast_hashing: None) -> Iterator[uuid.UUID]:
    """The admin account. The rest of the test runs as this user."""
    user_id = add_account(db, OWNER_EMAIL, OWNER_PASSWORD, admin=True)
    with tenant.as_user(user_id):
        yield user_id


@pytest.fixture
def sql(db: str, owner: uuid.UUID) -> Iterator[psycopg.Connection[Any]]:
    """The table owner's connection (it sees every row), inserting as the owner account."""
    with psycopg.connect(_libpq(db), autocommit=True) as conn:
        conn.execute(f"SET app.user_id = '{owner}'")
        yield conn


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Inngest events the API sent, instead of sending them."""
    sent: list[tuple[str, dict[str, Any]]] = []

    async def fake_send(name: str, data: dict[str, Any] | None = None) -> None:
        sent.append((name, data or {}))

    import functions.events

    monkeypatch.setattr(functions.events, "send", fake_send)
    for mod in (
        "api.profile",
        "api.resumes",
        "api.runs",
        "api.listings",
        "api.applications",
        "api.ops",
    ):
        monkeypatch.setattr(f"{mod}.send", fake_send)
    return sent


def _client() -> httpx.AsyncClient:
    from main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _sign_in(client: httpx.AsyncClient, email: str, password: str) -> None:
    resp = await client.post("/api/auth/sign-in", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text


@pytest.fixture
async def anon(db: str, events: list[Any]) -> AsyncIterator[httpx.AsyncClient]:
    """A browser with no session."""
    async with _client() as client:
        yield client
    await db_session.get_engine().dispose()


@pytest.fixture
async def api(anon: httpx.AsyncClient, owner: uuid.UUID) -> httpx.AsyncClient:
    """A browser signed in as the owner (PRD S1): every /api route but health needs one."""
    await _sign_in(anon, OWNER_EMAIL, OWNER_PASSWORD)
    return anon


@pytest.fixture
def other_id(db: str, owner: uuid.UUID) -> uuid.UUID:
    """A second account, not the admin (PRD S7)."""
    return add_account(db, OTHER_EMAIL, OTHER_PASSWORD)


@pytest.fixture
async def other(anon: httpx.AsyncClient, other_id: uuid.UUID) -> AsyncIterator[httpx.AsyncClient]:
    """A second browser, signed in as the second account."""
    async with _client() as client:
        await _sign_in(client, OTHER_EMAIL, OTHER_PASSWORD)
        yield client
