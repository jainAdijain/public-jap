"""Limits on the routes anyone can call without signing in (PRD S1, S4, S10).

Sign-up, forgot password and "send the link again" each send an email and write a row, so
an address or an email may only try so often in an hour. Tries are counted in Postgres
(rate_limit), in one-hour windows that health-watch deletes a day later, under a hash of
the address and a hash of the email: neither is stored. Over the limit the answer is 429 and
nothing else happens.

The visitor's address is the connection's own, unless JAP_CLIENT_IP_HEADER names the header
a trusted proxy sets (CF-Connecting-IP behind the Cloudflare Tunnel). It is never read from
a header otherwise: a header is whatever the caller typed.
"""

import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ApiError
from config import get_settings
from database.models import RateLimit

WINDOW = timedelta(hours=1)
TOO_MANY = "Too many tries. Wait a while and try again."


def client_ip(request: Request) -> str:
    header = get_settings().jap_client_ip_header.strip()
    if header:
        value = request.headers.get(header, "").split(",")[0].strip()
        if value:
            return value
    return request.client.host if request.client else "unknown"


def _key(route: str, kind: str, value: str) -> str:
    return f"{route}:{kind}:{hashlib.sha256(value.encode()).hexdigest()[:32]}"


def _window(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0)


async def _hit(session: AsyncSession, key: str, now: datetime) -> int:
    first = insert(RateLimit).values(key=key, window_start=_window(now), count=1)
    stmt = first.on_conflict_do_update(
        index_elements=["key", "window_start"], set_={"count": RateLimit.count + 1}
    ).returning(RateLimit.count)
    return int((await session.execute(stmt)).scalar_one())


async def check(session: AsyncSession, request: Request, route: str, email: str | None) -> None:
    """Count this try against the address and the email, and refuse it past either limit.
    The count is committed at once, so a refused or failed try still counts."""
    settings = get_settings()
    now = datetime.now(UTC)
    by_ip = await _hit(session, _key(route, "ip", client_ip(request)), now)
    by_email = await _hit(session, _key(route, "email", email.lower()), now) if email else 0
    await session.commit()
    if by_ip > settings.jap_auth_tries_per_hour_ip or (
        by_email > settings.jap_auth_tries_per_hour_email
    ):
        raise ApiError(429, "too_many_tries", TOO_MANY)
