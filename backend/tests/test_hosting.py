"""The two halves of the app, and where JAP is allowed to make a request (PRD H2, IDEA-13).

live runs the same image twice. The process the browser reaches must not mount the Inngest
endpoint at all, and the one that runs the functions must. And every request JAP makes to
the outside world has to land on an ATS, redirects included, because on a server the other
addresses reachable from the stack are the metadata service and the database.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from config import get_settings
from main import RUNS_FUNCTIONS, create_app
from sources import base
from sources.detect import HOSTS


@asynccontextmanager
async def _as_role(role: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    """A client onto a freshly built app of that role. Asking the app what it answers is
    the real check; this FastAPI keeps an included router nested, so reading app.routes
    would miss every path that matters."""
    monkeypatch.setenv("JAP_ROLE", role)
    get_settings.cache_clear()
    try:
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        get_settings.cache_clear()


async def test_the_api_process_does_not_mount_the_inngest_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _as_role("api", monkeypatch) as client:
        # Not refused by the guard: not there at all, so there is nothing to reach.
        assert (await client.get("/api/inngest")).status_code == 404
        # It is still the whole API, only without the functions.
        assert (await client.get("/api/health")).status_code == 200


@pytest.mark.parametrize("role", RUNS_FUNCTIONS)
async def test_the_worker_and_the_undivided_app_do_mount_it(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _as_role(role, monkeypatch) as client:
        assert (await client.get("/api/inngest")).status_code != 404


async def test_an_unknown_role_serves_the_api_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in JAP_ROLE must not quietly publish the function endpoint."""
    async with _as_role("wroker", monkeypatch) as client:
        assert (await client.get("/api/inngest")).status_code == 404
        assert (await client.get("/api/health")).status_code == 200


def test_every_host_a_pasted_url_may_name_is_one_JAP_may_fetch() -> None:
    """detect.py decides which board URLs are accepted and base.py which addresses may be
    reached; an ATS added to one and not the other would be refused at the first request."""
    assert set(HOSTS) <= base.ALLOWED_HOSTS


@pytest.mark.parametrize(
    "url",
    [
        "https://169.254.169.254/latest/meta-data/",  # the server's metadata service
        "https://db:5432/",  # the rest of the stack
        "https://evil.example/boards",
        "http://boards-api.greenhouse.io/v1/boards/x",  # right host, no TLS
    ],
)
async def test_a_board_cannot_send_jap_anywhere_else(url: str) -> None:
    async with base.client() as http:
        with pytest.raises(base.NotAllowed):
            await http.get(url)


async def test_a_redirect_off_the_ats_is_refused() -> None:
    """The first address is checked by detect.py; this is the hop after it."""
    away = "https://169.254.169.254/latest/meta-data/"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(302, headers={"location": away})
        return httpx.Response(200, json={"leaked": True})

    async with base.client() as http:
        http._transport = httpx.MockTransport(handler)
        with pytest.raises(base.NotAllowed):
            await http.get("https://boards-api.greenhouse.io/v1/boards/acme")


async def test_the_ats_itself_is_still_reachable() -> None:
    async with base.client() as http:
        http._transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={"ok": True}))
        resp = await http.get("https://boards-api.greenhouse.io/v1/boards/acme")
    assert resp.json() == {"ok": True}
