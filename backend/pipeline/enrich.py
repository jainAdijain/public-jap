"""Filling what the ATS did not give (I3, I4): model extraction and the dedup embedding.

Fields the ATS states directly (Ashby salary, Lever and Ashby workplace type) are copied
and never sent for extraction. The rest is extracted once per description: a posting whose
extracted_hash equals its description_hash is skipped, so an unchanged posting costs
nothing on the next run. The embedding is cached the same way and costs nothing anyway
(Ollama on the Mac).

A posting is shared, so reading it is nobody's work in particular (PRD S7): it is billed to
the service's budget, and nothing of any user's profile goes near it. The description is
redacted generically, the way any text from outside is. To keep that budget for jobs
someone wants, a posting is read only when its title is in a target role of at least one
user who follows its board (or one of them has no target roles, and so wants every job).
"""

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import BoardFollow, Posting, Profile, WatchedBoard
from llm import call
from llm.redact import Sensitive
from pipeline.resume import normalize_skills, skill_aliases
from pipeline.runs import in_scope, role_matcher
from schemas.extract import FIELDS, Extracted, from_output, output_model
from sources import ashby

MAX_JD_CHARS = 12_000  # about 3,000 tokens; longer descriptions are cut, which caps cost
EMBED_CHARS = 6_000  # nomic-embed-text reads 2,048 tokens by default in Ollama
WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}


def direct_fields(source: str, raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if source == "ashby":
        salary = ashby.salary(raw)
        if salary is not None:
            out["salary"] = salary.model_dump()
    if source in ("ashby", "lever"):
        mode = WORKPLACE.get(str(raw.get("workplaceType") or "").lower())
        if mode:
            out["work_mode"] = mode
    return out


@dataclass
class ExtractResult:
    skipped: bool
    fields: list[str]
    sent_to_model: str | None = None


async def wanted_posting(session: AsyncSession, posting_id: uuid.UUID) -> Posting | None:
    """The posting, or None when it is not to be read: it is gone, closed, or its company
    is no longer followed (I13). A read queued before an unfollow ends here, unspent."""
    posting = await session.get(Posting, posting_id)
    if posting is None or posting.state != "open":
        return None
    board = await session.get(WatchedBoard, posting.watched_board_id)
    return posting if board is not None and board.active else None


class Wanted:
    """Which titles on a board someone wants read: those in a target role of a user who
    follows it, or every title when one of them has no target roles."""

    def __init__(self, roles_of_followers: list[list[str] | None]) -> None:
        self.everything = any(not roles for roles in roles_of_followers)
        self.matchers = [role_matcher(roles) for roles in roles_of_followers if roles]

    def title(self, title: str) -> bool:
        return self.everything or any(in_scope(title, m) for m in self.matchers)


async def wanted_on(session: AsyncSession, board_id: uuid.UUID) -> Wanted:
    """Run as the service, which is what lets it read every follower's target roles; it
    reads nothing else of theirs."""
    rows = await session.execute(
        select(Profile.target_roles)
        .select_from(BoardFollow)
        .outerjoin(Profile, Profile.user_id == BoardFollow.user_id)
        .where(BoardFollow.board_id == board_id, BoardFollow.active)
    )
    return Wanted([roles for (roles,) in rows.all()])


async def extract_posting(session: AsyncSession, posting_id: uuid.UUID) -> ExtractResult:
    posting = await wanted_posting(session, posting_id)
    if posting is None:
        return ExtractResult(skipped=True, fields=[])
    if not (await wanted_on(session, posting.watched_board_id)).title(posting.title_raw):
        return ExtractResult(skipped=True, fields=[])
    if posting.extracted_hash == posting.description_hash:
        return ExtractResult(skipped=True, fields=[])  # cached by description hash (I4)
    description_hash = posting.description_hash
    direct = direct_fields(posting.source, posting.raw_payload)
    wanted = [f for f in FIELDS if f not in direct]
    sent: str | None = None
    values: dict[str, Any] = dict(direct)
    if wanted and posting.description_text.strip():
        result = await call(
            session,
            "extract",
            {
                "fields": ", ".join(wanted),
                "title": posting.title_raw,
                "locations": "; ".join(posting.locations) or "not stated",
                "description_text": posting.description_text[:MAX_JD_CHARS],
            },
            output_model(wanted),
            sensitive=Sensitive(),
            generic_redact=frozenset({"description_text"}),
        )
        sent = result.model_input
        values.update(from_output(result.output))
        if "skills" in values:
            values["skills"] = normalize_skills(values["skills"], await skill_aliases(session))
    extracted = Extracted.model_validate({**values, "direct": sorted(direct)})
    posting = await session.get(Posting, posting_id)
    assert posting is not None
    if posting.description_hash == description_hash:  # not changed under us meanwhile
        posting.extracted = extracted.model_dump(exclude_none=True)
        posting.extracted_hash = description_hash
        await session.commit()
    return ExtractResult(skipped=False, fields=wanted, sent_to_model=sent)


class EmbedFailed(Exception):
    pass


async def embed(texts: str) -> list[float]:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/embed",
                json={"model": settings.jap_model_embed, "input": texts},
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise EmbedFailed(f"Ollama did not embed: {type(exc).__name__}") from exc
    vectors = resp.json().get("embeddings") or []
    if not vectors or len(vectors[0]) != settings.jap_embed_dim:
        raise EmbedFailed(f"Ollama returned no {settings.jap_embed_dim}-dimension vector")
    return [float(x) for x in vectors[0]]


async def embed_posting(session: AsyncSession, posting_id: uuid.UUID) -> bool:
    """Embed title and description for near-match dedup (Phase 3). False when cached."""
    posting = await wanted_posting(session, posting_id)
    if posting is None or posting.embedded_hash == posting.description_hash:
        return False
    description_hash = posting.description_hash
    # nomic-embed-text expects a task prefix.
    doc = f"search_document: {posting.title_raw}\n{posting.description_text[:EMBED_CHARS]}"
    vector = await embed(doc)
    await session.execute(
        text(
            "UPDATE posting SET embedding = CAST(:v AS vector), embedded_hash = :h "
            "WHERE id = :id AND description_hash = :h"
        ),
        {
            "v": "[" + ",".join(f"{x:.7g}" for x in vector) + "]",
            "h": description_hash,
            "id": posting_id,
        },
    )
    await session.commit()
    return True
