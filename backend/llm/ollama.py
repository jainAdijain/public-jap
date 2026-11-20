"""Ollama serves the embedder. Locally it runs natively on the Mac, outside Docker, so it
can use the GPU; on the server it is a container in the live stack (compose.release.yaml).

`python -m llm.ollama` is `make ollama-check`. It runs inside the app container and reads
OLLAMA_BASE_URL from the same settings the pipeline uses, so it checks the address JAP will
really embed against, whichever of the two that is.
"""

import asyncio
import sys

import httpx

from schemas.health import Check


async def ping_ollama(base_url: str, model: str) -> Check:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        return Check(ok=False, detail=f"Not reachable: {type(exc).__name__}")
    names = {m.get("name", "") for m in resp.json().get("models", [])}
    if not any(n == model or n.startswith(f"{model}:") for n in names):
        return Check(ok=False, detail=f"Running, but {model} is not pulled (ollama pull {model})")
    return Check(ok=True, detail=f"Running, {model} ready")


async def main() -> int:
    from config import get_settings

    settings = get_settings()
    check = await ping_ollama(settings.ollama_base_url, settings.jap_model_embed)
    if check.ok:
        print(f"Ollama OK at {settings.ollama_base_url}: {check.detail}")
        return 0
    print(f"WARNING: Ollama at {settings.ollama_base_url}: {check.detail}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
