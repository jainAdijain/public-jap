"""GET /api/health: is the API up, and can it reach the database, Inngest and Ollama.

The route is open, because the things that need it cannot sign in: the uptime monitor that
says the site is down (O7), `infra/scripts/wait-for-health.sh` during a start or a deploy,
and Docker's own health checks. What it *says* depends on who is asking (IDEA-23, the
launch review). A stranger gets `ok` and nothing else; the per-service detail — which
environment this is, whether Ollama is pulled, whether Inngest is reachable — is a map of
the internals and a list of what to try next, so it goes only to:

- a caller inside the stack: loopback or a private address with no proxy headers, the same
  test /api/inngest uses (auth/guards.py), which is `make start` and the deploy script but
  never anything arriving through Cloudflare;
- a signed-in admin, who is the person the Health card in Settings is drawn for.

A signed-in user who is not an admin gets `env` as well, because the UI badge that says
"this is not prod" is drawn from it, and nothing more.
"""

import asyncio

from fastapi import APIRouter, Request

from auth import guards, sessions
from config import get_settings
from database.models import AppUser
from database.session import get_sessionmaker, ping_database
from functions.client import ping_inngest
from llm.ollama import ping_ollama
from schemas.health import Check, Health

router = APIRouter(tags=["health"])


async def _admin(request: Request) -> bool:
    """Whether a signed-in admin is asking. No database work at all without a session
    cookie, which is every anonymous caller: this route is polled every few seconds by the
    uptime monitor and by Docker, and none of that should cost a connection."""
    if not request.cookies.get(sessions.cookie_name(sessions.SIGNED_IN)):
        return False
    async with get_sessionmaker()() as session:
        row = await sessions.find(session, request, sessions.SIGNED_IN)
        if row is None:
            return False
        user = await session.get(AppUser, row.user_id)
        return user is not None and user.is_admin and user.disabled_at is None


@router.get("/health", response_model=Health)
async def health(request: Request) -> Health:
    settings = get_settings()
    database, inngest, ollama = await asyncio.gather(
        ping_database(),
        ping_inngest(settings.inngest_base_url),
        ping_ollama(settings.ollama_base_url, settings.jap_model_embed),
    )
    checks = {
        "api": Check(ok=True, detail="Responding"),
        "database": database,
        "inngest": inngest,
        "ollama": ollama,
    }
    ok = all(c.ok for c in checks.values())
    if guards.internal_call(request) or await _admin(request):
        return Health(ok=ok, env=settings.jap_env, checks=checks)
    # Signed in but not the admin: enough for the "this is not prod" badge, nothing more.
    if request.cookies.get(sessions.cookie_name(sessions.SIGNED_IN)):
        return Health(ok=ok, env=settings.jap_env)
    return Health(ok=ok)
