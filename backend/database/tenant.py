"""Whose data a database session may touch (PRD S7): one user's, the service's, or nobody's.

Every session carries a Tenant. A request's session gets it from the signed-in account
(auth/deps.py); background work gets it from the event that started it
(observability/inngest_mw.py) or says so itself with as_user() or as_system(). Code that
reads private rows asks user_id(session) and filters by it, and fails loudly when there is
no user rather than reading everyone's.

Postgres repeats the check (migration 0013_rls). At the start of every transaction the
session switches to the jap_app role and sets app.user_id (database/session.py); row-level
security on the private tables then shows only that user's rows, so a query written without
its filter returns nothing instead of another person's data. With no tenant at all the role
is still jap_app and app.user_id is empty: the private tables look empty. Only a system
tenant (reading shared job postings, migrations, the make targets) stays the table owner
and sees every row.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

APP_ROLE = "jap_app"
# Every table with a user_id and a row-level security policy (tests/test_tenancy.py checks
# the database agrees).
PRIVATE_TABLES = (
    "profile", "resume_version", "answer_bank", "application", "run", "board_follow",
    "user_listing", "llm_call", "user_secret",
)  # fmt: skip
INFO_KEY = "jap_tenant"
# The default of every user_id column and the test in every row-level security policy.
USER_ID_SQL = "(NULLIF(current_setting('app.user_id', true), '')::uuid)"
SCOPE_SQL = "SELECT set_config('role', :role, true), set_config('app.user_id', :uid, true)"


class NoTenant(RuntimeError):
    """Private data was asked for with no user in scope."""


@dataclass(frozen=True)
class Tenant:
    user_id: uuid.UUID | None = None
    system: bool = False

    def scope(self) -> dict[str, str]:
        """The role and app.user_id a transaction of this tenant runs with."""
        return {
            "role": "none" if self.system else APP_ROLE,
            "uid": str(self.user_id) if self.user_id else "",
        }


NOBODY = Tenant()
SYSTEM = Tenant(system=True)

_current: ContextVar[Tenant] = ContextVar("jap_tenant", default=NOBODY)


def user(user_id: uuid.UUID | str) -> Tenant:
    return Tenant(user_id=uuid.UUID(str(user_id)))


def bind(tenant: Tenant) -> None:
    """The tenant for the rest of this task (an Inngest call, a make target)."""
    _current.set(tenant)


@contextmanager
def as_user(user_id: uuid.UUID | str) -> Iterator[None]:
    token = _current.set(user(user_id))
    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def as_system() -> Iterator[None]:
    token = _current.set(SYSTEM)
    try:
        yield
    finally:
        _current.reset(token)


def of(session: Any) -> Tenant:
    """The session's own tenant (a request), else the task's."""
    found = session.info.get(INFO_KEY)
    return found if isinstance(found, Tenant) else _current.get()


def user_id(session: AsyncSession) -> uuid.UUID:
    tenant = of(session)
    if tenant.user_id is None:
        raise NoTenant("This needs a signed-in user, and there is none in scope")
    return tenant.user_id


async def use(session: AsyncSession, tenant: Tenant) -> None:
    """Make this the session's tenant from now on, the transaction in progress included."""
    session.info[INFO_KEY] = tenant
    if session.in_transaction():
        await session.execute(text(SCOPE_SQL), tenant.scope())


@asynccontextmanager
async def elevated(session: AsyncSession) -> AsyncIterator[None]:
    """The session as the service for a block: for the shared half of a user's action, such
    as closing a board's postings when its last follower leaves."""
    before = of(session)
    await use(session, SYSTEM)
    try:
        yield
    finally:
        await use(session, before)
