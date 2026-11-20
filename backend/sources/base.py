"""What every ATS adapter implements, and the three errors it may raise (design doc).

Every request JAP makes to the outside world goes through client() here, and the address it
may reach is an ATS and nothing else (IDEA-13). The first address is already checked — a
board URL is parsed against a list of known hosts (sources/detect.py) and an adapter builds
the rest itself — but the client follows redirects, and a redirect is an address JAP did
not choose. On a laptop that mattered little; on the server a redirect to 169.254.169.254
is the hosting provider's metadata service, and from there a redirect to db:5432 or
inngest:8288 is the rest of the stack. So the check is on every hop, not just the first.
"""

import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from schemas.job import Job

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
USER_AGENT = "JAP/0.3 (personal job search; one request per board per run)"

# The only hosts JAP ever fetches from: the three ATS APIs the adapters call, and the board
# pages a user may paste, which sources/detect.py parses. tests/test_sources.py checks this
# covers every host in detect.HOSTS, so adding an ATS to one and not the other fails there.
ALLOWED_HOSTS = frozenset(
    {
        "boards-api.greenhouse.io",
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "job-boards.eu.greenhouse.io",
        "api.lever.co",
        "jobs.lever.co",
        "api.ashbyhq.com",
        "jobs.ashbyhq.com",
    }
)


class NotAllowed(httpx.HTTPError):
    """A request, usually a redirect, aimed somewhere that is not an ATS."""


def _check(request: httpx.Request) -> None:
    url = request.url
    host = (url.host or "").lower().removeprefix("www.")
    if url.scheme != "https":
        raise NotAllowed(f"{url.scheme}:// is not https")
    if host not in ALLOWED_HOSTS:
        raise NotAllowed(f"{host} is not a job board JAP reads")
    # A name that resolves to the machine itself or the private network would get past the
    # list only by DNS, but the check is cheap and says what it refuses.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise NotAllowed(f"{host} is not a public address")


class BoardNotFound(Exception):
    """404: the board does not exist. The board is marked last_ok = false, no retry."""


class SourceUnavailable(Exception):
    """5xx, 429 or a timeout. Inngest retries the step (2 retries)."""


class SourceChanged(Exception):
    """The payload no longer fits Job. The step fails and the run says why."""


@dataclass
class BoardInfo:
    name: str | None  # the company name the ATS shows, when it has one
    job_count: int


@dataclass
class Adapter:
    host: str  # for the 1 request per second per host throttle (I7)
    fetch: Callable[[httpx.AsyncClient, str, str], Awaitable[list[Job]]]
    board_info: Callable[[httpx.AsyncClient, str], Awaitable[BoardInfo]]


async def _guard(request: httpx.Request) -> None:
    _check(request)


def client() -> httpx.AsyncClient:
    """The one client for reading job boards. The hook runs for the first request and for
    every redirect after it, so a board cannot send JAP anywhere else."""
    return httpx.AsyncClient(
        timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        event_hooks={"request": [_guard]},
    )


async def get_json(http: httpx.AsyncClient, url: str) -> Any:
    try:
        resp = await http.get(url)
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"{url}: {type(exc).__name__}") from exc
    if resp.status_code == 404:
        raise BoardNotFound(url)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise SourceUnavailable(f"{url}: HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise SourceChanged(f"{url}: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise SourceChanged(f"{url}: response is not JSON") from exc


def build(
    source: str, mapper: Callable[[dict[str, Any], str], Job], job: dict[str, Any], name: str
) -> Job:
    """Map one payload to Job, turning any mismatch into SourceChanged."""
    try:
        return mapper(job, name)
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise SourceChanged(f"{source} payload no longer fits Job: {exc}") from exc
