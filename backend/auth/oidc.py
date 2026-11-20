"""Signing in with Google (PRD S16): the OpenID Connect authorization-code flow with PKCE.

The browser is sent to Google, comes back to /api/auth/google/callback with a code, and the
code is exchanged for an ID token **server to server**. Everything that matters is checked
on the way back: state against the cookie (so the callback belongs to the request that
started it), the code verifier (PKCE, so a stolen code is no use without it), and then the
token's own issuer, audience, expiry and nonce.

**The ID token's signature is not checked here, and that is deliberate.** This is the
authorization-code flow: the token is not read out of the browser's URL, it is fetched by
JAP from Google's token endpoint over TLS, with the client secret, in a request nobody else
can answer. OpenID Connect Core 3.1.3.7 says in so many words that TLS server validation
may stand in place of checking the signature when the token comes through that channel, and
Google's own documentation says the same. Verifying RS256 here would mean a JWKS cache and
adding `cryptography` to the service for nothing. The rule that makes it sound is narrow:
an ID token is only ever read from a token-endpoint response (`exchange` below). If a path
is ever added that accepts one from a browser, it needs the signature checked.

The discovery document is read once and kept, because its endpoints change about never, and
the whole thing is skipped when GOOGLE_* is unset: the routes then answer Not Found.
"""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from config import get_settings

SCOPE = "openid email profile"
TIMEOUT = 10.0
# A sign-in that has been sitting at Google's page longer than this starts again.
START_SECONDS = 15 * 60
_discovery: dict[str, Any] | None = None


class OidcError(Exception):
    """Google said no, or said something unusable. The message is for the log, not the user."""


@dataclass(frozen=True)
class Identity:
    """Who Google says this is."""

    subject: str  # their id for this person: stable, and never reused for someone else
    email: str
    email_verified: bool


def client() -> httpx.AsyncClient:
    """The HTTP client for Google, in one place so a test can stand in for it, the way
    sources/base.py does for the job boards. make test reaches no network."""
    return httpx.AsyncClient(timeout=TIMEOUT)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def new_verifier() -> str:
    """The PKCE code verifier: kept in a cookie, never sent to Google until the exchange."""
    return _b64url(secrets.token_bytes(48))


def challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode()).digest())


async def discovery() -> dict[str, Any]:
    """Google's endpoint list, read once per process."""
    global _discovery
    if _discovery is None:
        url = f"{get_settings().google_issuer_url.rstrip('/')}/.well-known/openid-configuration"
        async with client() as http:
            resp = await http.get(url)
        if resp.status_code != 200:
            raise OidcError(f"discovery answered {resp.status_code}")
        _discovery = dict(resp.json())
    return _discovery


def forget_discovery() -> None:
    """Drop the cached document (a test pointing at a stand-in, config changing)."""
    global _discovery
    _discovery = None


async def start_url(state: str, nonce: str, verifier: str) -> str:
    """Where to send the browser."""
    settings = get_settings()
    endpoint = str((await discovery())["authorization_endpoint"])
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_url,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge(verifier),
            "code_challenge_method": "S256",
            # Ask for the account chooser rather than silently reusing one signed-in Google
            # account, which is surprising on a shared computer.
            "prompt": "select_account",
        }
    )
    return f"{endpoint}?{query}"


def _claims(id_token: str) -> dict[str, Any]:
    """The payload of a JWT, without checking its signature (see the module docstring)."""
    parts = id_token.split(".")
    if len(parts) != 3:
        raise OidcError("the ID token is not a JWT")
    payload = parts[1]
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        out = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise OidcError("the ID token payload is not readable") from exc
    if not isinstance(out, dict):
        raise OidcError("the ID token payload is not an object")
    return out


def _checked(claims: dict[str, Any], nonce: str, now: float) -> Identity:
    """Issuer, audience, expiry and nonce, then who it says this is."""
    settings = get_settings()
    issuer = str(claims.get("iss") or "")
    # Google issues both spellings for the same issuer.
    allowed = {settings.google_issuer_url.rstrip("/")}
    if allowed == {"https://accounts.google.com"}:
        allowed.add("accounts.google.com")
    if issuer.rstrip("/") not in allowed:
        raise OidcError(f"the ID token is from {issuer!r}")
    audience = claims.get("aud")
    auds = audience if isinstance(audience, list) else [audience]
    if settings.google_client_id not in auds:
        raise OidcError("the ID token is for another client")
    expires = claims.get("exp")
    if not isinstance(expires, int | float) or expires <= now:
        raise OidcError("the ID token has expired")
    if claims.get("nonce") != nonce:
        raise OidcError("the ID token is for another sign-in")
    subject = str(claims.get("sub") or "")
    email = str(claims.get("email") or "").strip().lower()
    if not subject or not email:
        raise OidcError("the ID token names no account")
    return Identity(subject=subject, email=email, email_verified=bool(claims.get("email_verified")))


async def exchange(code: str, verifier: str, nonce: str) -> Identity:
    """Swap the code for an ID token and read who it is. The only place an ID token is
    accepted, and it comes straight from the token endpoint (see the module docstring)."""
    settings = get_settings()
    endpoint = str((await discovery())["token_endpoint"])
    async with client() as http:
        resp = await http.post(
            endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.google_redirect_url,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "code_verifier": verifier,
            },
        )
    if resp.status_code != 200:
        raise OidcError(f"the token endpoint answered {resp.status_code}")
    id_token = str(resp.json().get("id_token") or "")
    if not id_token:
        raise OidcError("the token endpoint returned no ID token")
    return _checked(_claims(id_token), nonce, time.time())
