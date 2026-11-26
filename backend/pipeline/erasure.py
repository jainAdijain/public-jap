"""Export my data, and delete my account (PRD S15).

Two halves of the same promise: a person can have back everything JAP holds about them, and
can have all of it gone. Both walk `tenant.PRIVATE_TABLES`, the one list of tables that
carry a user_id, so a private table added later cannot be quietly left out of either — a
test asserts every name in it has a file here and a place in the deletion
(tests/test_account_delete.py).

**Export** runs as the user, so row-level security is the thing that guarantees the zip
holds nobody else's rows, not the care taken writing the queries. Ids of shared rows are
kept, because they are what joins the files to each other, and each job, company and
application is given its title and company name as well, so the zip reads as something
about a person's job hunt rather than a page of UUIDs.

**Deletion** runs as the service, because it has to reach past row-level security (the
admin may be deleting someone else's account) and touch the shared half: when the last
follower of a company leaves, that board stops being fetched. Running as the service means
every statement here must name its user_id itself; there is no second lock behind it.

Two things survive a deletion, and the notice says so:

- `llm_call` rows, with user_id nulled and billed_to 'deleted'. They hold no personal
  content — purpose, model, cost — and they are what the OpenRouter bill is counted from.
- `log_event` lines, with user_id nulled. Already scrubbed of personal values when written
  (observability/scrub.py), already deleted after 30 days, and the service's own record of
  what it did.

Everything else about the person goes, rows and files, in one transaction. Files are
removed after it commits: the filesystem cannot be rolled back, so a deletion that fails
must not have taken them.
"""

import asyncio
import json
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.base import Base
from database.models import (
    AlertState,
    Application,
    AppUser,
    AuthSession,
    Company,
    EmailVerification,
    Listing,
    LlmCall,
    LogEvent,
    PasswordReset,
    ResumeVersion,
    UserIdentity,
    WatchedBoard,
)
from pipeline import storage
from pipeline.unfollow import close_board, followers

# One file per private table (tenant.PRIVATE_TABLES), named for what it is to the reader
# rather than for the table it came from.
FILES: dict[str, str] = {
    "profile": "profile.json",
    "resume_version": "resumes.json",
    "answer_bank": "answers.json",
    "application": "applications.json",
    "run": "searches.json",
    "board_follow": "companies-followed.json",
    "user_listing": "jobs.json",
    "llm_call": "ai-spend.json",
    "user_secret": "api-keys.json",
    "subscription": "plan.json",
}

# Left out of the export: a hash of what was sent to a model tells the reader nothing and
# is not a fact about them. Everything else in every table is theirs and goes in.
LEAVE_OUT: dict[str, tuple[str, ...]] = {
    "llm_call": ("input_sha256",),
    # A code a job site emailed, while a form is waiting for it (IDEA-16). It is gone
    # seconds later and is a credential, not a fact about the person.
    "application": ("security_code",),
    # The sealed key and its nonce are not a fact about the person, and handing back
    # ciphertext would be worse than useless. The hint and the dates say a key was set.
    "user_secret": ("ciphertext", "nonce"),
}

# Deleted outright, in an order the foreign keys allow: an application points at its run
# and its resume version, so it goes before both.
DELETE_ORDER = (
    "application", "run", "user_listing", "board_follow", "answer_bank", "resume_version",
    "profile", "user_secret", "subscription",
)  # fmt: skip
# Kept, with the person taken out of them (the module docstring says why).
ANONYMISED = ("llm_call",)

# Tables outside PRIVATE_TABLES that still name this account. app_user goes last, and
# every one of these has to go before it: each has a foreign key to it with no cascade.
SIGN_IN_TABLES = (AuthSession, PasswordReset, EmailVerification, UserIdentity)


@dataclass
class Erased:
    """What a deletion removed, for the log line. No content, only counts."""

    user_id: uuid.UUID
    rows: dict[str, int] = field(default_factory=dict)
    files: int = 0
    boards_closed: int = 0


def _plain(value: Any) -> Any:
    """A value as JSON holds it."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes | memoryview):
        return None  # no private table stores bytes; this is here so one added later cannot break
    return value


async def _rows(session: AsyncSession, table_name: str, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Every row of one private table belonging to this user, as plain dictionaries."""
    table = Base.metadata.tables[table_name]
    leave_out = LEAVE_OUT.get(table_name, ())
    columns = [c for c in table.columns if c.name not in leave_out]
    result = await session.execute(select(*columns).where(table.c.user_id == user_id))
    return [{k: _plain(v) for k, v in row._mapping.items()} for row in result]


async def _listing_facts(session: AsyncSession, ids: set[uuid.UUID]) -> dict[str, dict[str, Any]]:
    """Each job's title and company, so a row about a job says which job."""
    if not ids:
        return {}
    rows = await session.execute(
        select(Listing.id, Listing.title_display, Company.name)
        .join(Company, Company.id == Listing.company_id, isouter=True)
        .where(Listing.id.in_(ids))
    )
    return {str(i): {"title": title, "company": company} for i, title, company in rows}


async def _board_facts(session: AsyncSession, ids: set[uuid.UUID]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    rows = await session.execute(
        select(WatchedBoard.id, WatchedBoard.display_name, WatchedBoard.ats).where(
            WatchedBoard.id.in_(ids)
        )
    )
    return {str(i): {"company": name, "ats": ats} for i, name, ats in rows}


def _ids(rows: list[dict[str, Any]], key: str) -> set[uuid.UUID]:
    return {uuid.UUID(r[key]) for r in rows if r.get(key)}


async def collect(session: AsyncSession, user_id: uuid.UUID) -> dict[str, list[dict[str, Any]]]:
    """Every private row of this user, by the file it belongs in."""
    tables = {name: await _rows(session, name, user_id) for name in FILES}
    listings = _ids(tables["user_listing"], "listing_id")
    listings |= _ids(tables["application"], "listing_id")
    facts = await _listing_facts(session, listings)
    boards = await _board_facts(session, _ids(tables["board_follow"], "board_id"))
    for row in tables["user_listing"] + tables["application"]:
        row["job"] = facts.get(str(row.get("listing_id")))
    for row in tables["board_follow"]:
        row.update(boards.get(str(row.get("board_id")), {}))
    return {FILES[name]: rows for name, rows in tables.items()}


async def my_files(session: AsyncSession, user_id: uuid.UUID) -> list[Path]:
    """Every file this account's rows point at and that is still inside data/: resumes,
    cover letters, screenshots and traces. Found by the row, never by a glob, so a file
    belonging to someone else cannot be swept in by a path that looks similar."""
    resumes = select(ResumeVersion.file_path).where(ResumeVersion.user_id == user_id)
    named: list[str | None] = list(await session.scalars(resumes))
    rows = await session.execute(
        select(
            Application.screenshot_path, Application.trace_path, Application.cover_letter_path
        ).where(Application.user_id == user_id)
    )
    for row in rows:
        named.extend(row)
    out: list[Path] = []
    seen: set[Path] = set()
    for path in named:
        found = storage.served(path)
        if found is not None and found not in seen:
            seen.add(found)
            out.append(found)
    return out


def _write_zip(target: Path, data: dict[str, list[dict[str, Any]]], files: list[Path],
               user_dir: Path, email: str, made_at: datetime) -> int:  # fmt: skip
    """The zip itself, and its size. Blocking, so export() runs it off the event loop."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", _readme(data, email, made_at))
        for name, rows in data.items():
            zf.writestr(name, json.dumps(rows, indent=2, ensure_ascii=False, sort_keys=True))
        written: set[str] = set()
        for path in files:
            # Under the account's own folder the shape is kept (files/screenshots/x.png);
            # a file written before each account had one keeps only its name.
            mine = path.is_relative_to(user_dir)
            name = str(Path("files") / (path.relative_to(user_dir) if mine else path.name))
            if name in written:
                continue
            written.add(name)
            zf.write(path, name)
    return target.stat().st_size


def _readme(data: dict[str, list[dict[str, Any]]], email: str, made_at: datetime) -> str:
    lines = [
        "Your JAP data",
        "=============",
        "",
        f"Account: {email}",
        f"Made:    {made_at.isoformat(timespec='seconds')}",
        "",
        "Everything JAP holds about you. Each file is a list of rows, as JSON.",
        "",
    ]
    what = {
        "profile.json": "Your profile and job preferences, and what you have agreed to.",
        "resumes.json": "Every resume you uploaded, with the text and skills read out of it.",
        "answers.json": "Your saved answers to employers' questions.",
        "applications.json": "Every application: the frozen job description, the form, your "
        "answers, the cover letter and the outcome.",
        "searches.json": "Every search, what it found and any error.",
        "companies-followed.json": "The companies you follow.",
        "jobs.json": "Every job that matched you: the skills match, your status, when you applied.",
        "ai-spend.json": "What each AI call cost, and what it was for. Never what was sent.",
        "api-keys.json": "Which of your own API keys are saved, and when. Never the keys "
        "themselves: they are encrypted and are not handed back.",
        "files/": "Your resumes, the cover letters written for you, and a screenshot and "
        "recording of each form JAP filled in.",
    }
    for name, description in what.items():
        count = f" ({len(data[name])} rows)" if name in data else ""
        lines.append(f"{name}{count}")
        lines.append(f"    {description}")
    lines += [
        "",
        "Jobs, companies and postings themselves are facts about the job market and are "
        "shared by everyone using JAP, so only your side of them is here.",
        "",
    ]
    return "\n".join(lines)


async def export(session: AsyncSession, user: AppUser, target: Path) -> int:
    """Write this account's zip to target and return its size. Runs as the user: row-level
    security is what keeps anybody else's rows out of it, not the care taken here."""
    data = await collect(session, user.id)
    files = await my_files(session, user.id)
    return int(
        await asyncio.to_thread(
            _write_zip,
            target,
            data,
            files,
            storage.user_dir(user.id),
            user.email,
            datetime.now(UTC),
        )
    )


async def erase(session: AsyncSession, user_id: uuid.UUID) -> Erased:
    """Delete an account and everything private about it, in one transaction, then its
    files. The caller has already checked who is asking; this does not ask again.

    Run as the service (tenant.SYSTEM): every statement names the user itself."""
    out = Erased(user_id=user_id)
    paths = await my_files(session, user_id)
    user_dir = storage.user_dir(user_id)
    follow = Base.metadata.tables["board_follow"]
    boards = set(
        await session.scalars(select(follow.c.board_id).where(follow.c.user_id == user_id))
    )
    # Only for this transaction, and only here: it lets an Applied application be deleted
    # (migration 0016_erasure). Everything else still meets the frozen rule.
    await session.execute(text("SET LOCAL app.erasing = 'on'"))
    anonymised = await session.scalars(
        update(LlmCall)
        .where(LlmCall.user_id == user_id)
        # run_id has a foreign key to a run that is about to go, so it cannot stay.
        .values(user_id=None, billed_to="deleted", run_id=None, application_id=None)
        .returning(LlmCall.id)
    )
    out.rows["llm_call"] = len(list(anonymised))
    await session.execute(update(LogEvent).where(LogEvent.user_id == user_id).values(user_id=None))
    await session.execute(delete(AlertState).where(AlertState.user_id == user_id))
    for name in DELETE_ORDER:
        table = Base.metadata.tables[name]
        gone = await session.scalars(
            delete(table).where(table.c.user_id == user_id).returning(table.c.user_id)
        )
        out.rows[name] = len(list(gone))
    for model in SIGN_IN_TABLES:
        await session.execute(delete(model).where(model.user_id == user_id))
    # The shared half: a company nobody follows any more stops being fetched.
    for board_id in boards:
        if await followers(session, board_id) == 0:
            board = await session.get(WatchedBoard, board_id, with_for_update=True)
            if board is not None and board.active:
                board.active = False
                await close_board(session, board_id)
                out.boards_closed += 1
    await session.execute(delete(AppUser).where(AppUser.id == user_id))
    await session.commit()
    # Only now the files: a transaction that failed above must not have taken them.
    for path in paths:
        path.unlink(missing_ok=True)
        out.files += 1
    shutil.rmtree(user_dir, ignore_errors=True)
    return out
