"""Export my data, and delete my account (PRD S15).

These two are deliberately **not** behind the terms gate the rest of /api carries
(auth/deps.py: agreed_user). "I don't agree to your new notice, so give me my data and
delete my account" has to be something a person can actually do; refusing it until they
agree would make the gate a trap. They carry current_user instead, and main.py registers
this router on its own for that reason.

Deleting asks for three things — the password, the two-step code when it is on, and the
account's own address typed out — because it happens at once and there is no undo. The
work itself is pipeline/erasure.py; this file is the asking.
"""

import asyncio
import logging
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from api.auth import check_password, use_code
from api.errors import ApiError
from auth import limits, sessions
from auth.deps import AdminUser, CurrentUser
from billing import service as billing
from database import tenant
from database.models import AppUser
from database.session import Session
from observability.logs import event
from pipeline import erasure
from schemas.account import DeleteAccount, Deleted

router = APIRouter(tags=["account"])
log = logging.getLogger("jap.account")

ADMIN_STAYS = "The admin account can't be deleted"


def _unlink(path: Path) -> None:
    path.unlink(missing_ok=True)


@router.get("/account/export")
async def export_my_data(user: CurrentUser, request: Request, session: Session) -> FileResponse:
    """A zip of everything JAP holds about this account (S15). Limited per hour, because it
    reads every row the account owns and copies every file."""
    await limits.check(session, request, "export", user.email)
    handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)  # noqa: SIM115
    handle.close()
    target = Path(handle.name)
    try:
        size = await erasure.export(session, user, target)
    except Exception:
        await asyncio.to_thread(_unlink, target)
        raise
    event(log, "account.exported", "Exported this account's data", bytes=size)
    return FileResponse(
        target,
        media_type="application/zip",
        filename=f"jap-{datetime.now(UTC).date().isoformat()}.zip",
        # The temp file goes once the response has been sent, whatever happens to it.
        background=BackgroundTask(_unlink, target),
    )


async def _erase(session: AsyncSession, user_id: uuid.UUID, who: str) -> None:
    """The deletion itself, as the service: it reaches past row-level security, which the
    admin deleting somebody else needs, and closes boards nobody follows any more."""
    await tenant.use(session, tenant.SYSTEM)
    # Before the rows go: stop the mandate, or the provider would go on taking money from
    # somebody who no longer has an account here (IDEA-21). Best effort by design — a
    # provider that is down must not be able to block a deletion.
    await billing.cancel_quietly(session, user_id)
    done = await erasure.erase(session, user_id)
    event(
        log,
        "account.deleted",
        f"Account deleted by {who}",
        keep=True,
        rows=sum(done.rows.values()),
        files=done.files,
        boards_closed=done.boards_closed,
    )


@router.post("/account/delete", response_model=Deleted)
async def delete_my_account(
    body: DeleteAccount, user: CurrentUser, request: Request, response: Response, session: Session
) -> Deleted:
    """Delete this account and everything private about it, at once and for good (S15)."""
    await limits.check(session, request, "account-delete", user.email)
    if user.is_admin:
        raise ApiError(409, "conflict", ADMIN_STAYS)
    # An account that signs in with Google only has no password to ask for (S16); the live
    # session and typing the address out are what it has instead.
    if user.password_hash:
        await check_password(session, user, body.password)
    if user.totp_secret is not None and not use_code(user, body.code):
        raise ApiError(403, "wrong_code", "That code didn't work")
    if body.confirm_email.strip().lower() != user.email.lower():
        raise ApiError(422, "email_mismatch", "Type the email address of this account to confirm")
    await _erase(session, user.id, "the account holder")
    sessions.clear_cookie(response, sessions.SIGNED_IN)
    return Deleted()


admin_router = APIRouter(prefix="/admin", tags=["account"])


@admin_router.delete("/accounts/{user_id}", response_model=Deleted)
async def delete_account(user_id: uuid.UUID, admin: AdminUser, session: Session) -> Deleted:
    """Delete an account for a user who asked by email (S15). The same erasure, and the
    same refusal for the admin's own account."""
    user = await session.get(AppUser, user_id)
    if user is None:
        raise ApiError(404, "not_found", "No such account")
    if user.is_admin:
        raise ApiError(409, "conflict", ADMIN_STAYS)
    await _erase(session, user_id, f"the admin ({admin.email})")
    return Deleted()
