"""Cloudflare Turnstile, the "prove you're human" check on sign-up and forgot password.

The page shows the widget with TURNSTILE_SITE_KEY and sends the token it gets; this asks
Cloudflare whether the token is good, with TURNSTILE_SECRET_KEY. With no secret set (local,
tests) there is no check. When Cloudflare can't be reached the try is refused: a check that
passes whenever it fails to run would stop nobody.
"""

import httpx

from api.errors import ApiError
from config import get_settings

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
FAILED = "The check that you're human didn't pass. Try again."


async def passed(token: str | None, ip: str) -> bool:
    secret = get_settings().turnstile_secret_key
    if not secret:
        return True
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                VERIFY_URL, data={"secret": secret, "response": token, "remoteip": ip}
            )
            resp.raise_for_status()
            return bool(resp.json().get("success"))
    except (httpx.HTTPError, ValueError):
        return False


async def check(token: str | None, ip: str) -> None:
    if not await passed(token, ip):
        raise ApiError(403, "not_human", FAILED)
