"""Async engine and session factory for Postgres (psycopg 3).

Every transaction starts by taking its tenant's role and app.user_id (database/tenant.py),
which is what row-level security on the private tables reads (PRD S7).
"""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.orm import SessionTransaction

from config import get_settings
from database import tenant
from schemas.health import Check


class TenantSession(SyncSession):
    """A session whose every transaction is scoped to its tenant."""


@event.listens_for(TenantSession, "after_begin")
def _scope(session: SyncSession, transaction: SessionTransaction, connection: Connection) -> None:
    if not transaction.nested:
        connection.execute(text(tenant.SCOPE_SQL), tenant.of(session).scope())


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        get_engine(), expire_on_commit=False, sync_session_class=TenantSession
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request. It belongs to nobody until the request's
    account is known (auth/deps.py), whatever the task it runs in was doing before."""
    async with get_sessionmaker()() as session:
        session.info[tenant.INFO_KEY] = tenant.NOBODY
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


async def ping_database() -> Check:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
            ext = await conn.execute(
                text(
                    "SELECT count(*) FROM pg_extension"
                    " WHERE extname IN ('vector', 'pg_trgm', 'pgcrypto')"
                )
            )
            found = ext.scalar_one()
    except Exception as exc:  # noqa: BLE001 - any failure means not reachable
        return Check(ok=False, detail=f"Not reachable: {type(exc).__name__}")
    if found < 3:
        return Check(ok=False, detail=f"Connected, but only {found} of 3 extensions installed")
    return Check(ok=True, detail="Connected, pgvector ready")
