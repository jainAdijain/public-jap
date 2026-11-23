import pytest
from fastapi.testclient import TestClient

import api.health as health_module
from config import get_settings
from main import app
from schemas.health import Check


def _ok(detail: str) -> Check:
    return Check(ok=True, detail=detail)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def db() -> Check:
        return _ok("db")

    async def inngest(_: str) -> Check:
        return _ok("inngest")

    async def ollama(_: str, __: str) -> Check:
        return _ok("ollama")

    monkeypatch.setattr(health_module, "ping_database", db)
    monkeypatch.setattr(health_module, "ping_inngest", inngest)
    monkeypatch.setattr(health_module, "ping_ollama", ollama)
    return TestClient(app)


def test_health_all_ok(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert set(body["checks"]) == {"api", "database", "inngest", "ollama"}
    assert body["env"] == "dev"


def test_health_tells_a_stranger_only_whether_it_is_up(client: TestClient) -> None:
    """IDEA-23: through Cloudflare, /api/health is not a map of the internals.

    A proxy header is what makes a call external (auth/guards.internal_call), which is
    exactly the position of anything arriving over the tunnel.
    """
    body = client.get("/api/health", headers={"CF-Connecting-IP": "203.0.113.9"}).json()
    # Still a usable uptime check: ok, and a 200.
    assert body["ok"] is True
    # But not which environment this is, nor the state of any one service. The keys stay,
    # emptied rather than dropped, so the UI's health?.checks[k] cannot read undefined.
    assert body["env"] == ""
    assert body["checks"] == {}


def test_health_still_says_everything_to_the_stack_itself(client: TestClient) -> None:
    """wait-for-health.sh, the deploy script and Docker's health check must keep working:
    they call from loopback with no proxy headers."""
    body = client.get("/api/health").json()
    assert set(body["checks"]) == {"api", "database", "inngest", "ollama"}
    assert body["env"] == "dev"


def test_health_reports_ollama_down(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def ollama_down(_: str, __: str) -> Check:
        return Check(ok=False, detail="Not reachable: ConnectError")

    monkeypatch.setattr(health_module, "ping_ollama", ollama_down)
    body = client.get("/api/health").json()
    assert body["ok"] is False
    assert body["checks"]["ollama"]["ok"] is False
    assert body["checks"]["database"]["ok"] is True


def test_cors_allows_only_ui_origin(client: TestClient) -> None:
    # This stack's own UI, whichever port it is on: a worktree is not 5173.
    ui = get_settings().cors_origins[0]
    allowed = client.get("/api/health", headers={"Origin": ui})
    assert allowed.headers.get("access-control-allow-origin") == ui
    other = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_inngest_endpoint_lists_functions(client: TestClient) -> None:
    body = client.get("/api/inngest").json()
    assert body["function_count"] >= 1
