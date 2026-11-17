"""Loads seed/companies.yaml and seed/skills.yaml (make seed, make reset).

Upserts, so it is safe to repeat: boards you removed in the UI stay removed, and
aliases are refreshed from the file.

The seeded companies are the admin's starting list (PRD S1, S7). A board is shared and is
fetched only while someone follows it, so a new board is followed for the admin when there
is one, and waits switched off for the first account to follow otherwise (auth/accounts.py).
Nobody else starts with them.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.dialects.postgresql import insert

from database import tenant
from database.models import BoardFollow, SkillAlias, WatchedBoard
from database.session import get_sessionmaker
from pipeline.workspace import admin_id

# In the app container seed/ is mounted at /seed; elsewhere it sits at the repo root.
SEED_DIR = Path(os.environ.get("JAP_SEED_DIR", Path(__file__).resolve().parents[2] / "seed"))
ATS_KEYS = {"greenhouse", "lever", "ashby"}


def load(name: str) -> Any:
    with (SEED_DIR / name).open() as f:
        return yaml.safe_load(f) or {}


def check_companies(data: Any) -> int:
    if not isinstance(data, dict) or not set(data) <= ATS_KEYS:
        raise ValueError(f"companies.yaml: top-level keys must be among {sorted(ATS_KEYS)}")
    count = 0
    for ats, boards in data.items():
        for board in boards or []:
            if not isinstance(board, dict) or not {"token", "name"} <= set(board):
                raise ValueError(f"companies.yaml: {ats} entry needs token and name: {board!r}")
            count += 1
    return count


def check_skills(data: Any) -> int:
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError("skills.yaml: must map alias -> canonical name")
    return len(data)


async def upsert(companies: Any, skills: dict[str, str]) -> None:
    with tenant.as_system():
        await _upsert(companies, skills)


async def _upsert(companies: Any, skills: dict[str, str]) -> None:
    async with get_sessionmaker()() as session:
        admin = await admin_id(session)
        for ats, boards in companies.items():
            for board in boards or []:
                new = await session.scalar(
                    insert(WatchedBoard)
                    .values(
                        ats=ats,
                        board_token=str(board["token"]).lower(),
                        display_name=board["name"],
                        origin="seed",
                        active=admin is not None,
                    )
                    .on_conflict_do_nothing(index_elements=["ats", "board_token"])
                    .returning(WatchedBoard.id)
                )
                if new is not None and admin is not None:
                    await session.execute(
                        insert(BoardFollow).values(user_id=admin, board_id=new, active=True)
                    )
        for alias, canonical in skills.items():
            stmt = insert(SkillAlias).values(alias=alias.lower(), canonical=canonical.lower())
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["alias"], set_={"canonical": stmt.excluded.canonical}
                )
            )
        await session.commit()


def main() -> int:
    companies = load("companies.yaml")
    skills = load("skills.yaml")
    boards = check_companies(companies)
    aliases = check_skills(skills)
    asyncio.run(upsert(companies, skills))
    print(f"Seeded {boards} boards and {aliases} skill aliases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
