"""Hardening on the Status card (Phase 8): the daily backup and the weekly form test.

GET /api/ops shows the last backup and each ATS's last form test; the two POSTs start a
backup or a form test now (the same Inngest functions the schedules run). GET /api/ops/status
is the health part of Status (PRD 1.19, O5). All of it is about the service, so the whole
router is the admin's (main.py; PRD S12, O Part B).
"""

import uuid
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from api.errors import ApiError
from database.models import FormCheck
from database.session import Session
from functions.events import send
from observability import status as health
from observability.capacity import DEFAULT_DAYS
from observability.capacity import capacity as read_capacity
from pipeline import backup, form_check, storage
from schemas.ops import BackupOut, Capacity, FormCheckOut, Ops
from schemas.status import Status

router = APIRouter(prefix="/ops", tags=["ops"])


def _check_out(row: FormCheck) -> FormCheckOut:
    return FormCheckOut(
        id=row.id,
        ats=row.ats,
        ok=row.ok,
        fields=row.fields,
        required=row.required,
        url=row.url,
        error=row.error,
        has_screenshot=bool(row.screenshot_path) and Path(str(row.screenshot_path)).exists(),
        checked_at=row.created_at,
    )


@router.get("", response_model=Ops)
async def ops(session: Session) -> Ops:
    files = backup.backups()
    last = files[0] if files else None
    encrypted, detail = backup.age_ready()
    return Ops(
        last_backup=BackupOut(
            file=last.path.name, at=last.at, bytes=last.size, encrypted=last.encrypted
        )
        if last
        else None,
        backups=len(files),
        backup_today=backup.has_today(),
        backups_encrypted=encrypted,
        backups_encryption=detail,
        form_checks=[_check_out(r) for r in await form_check.latest(session)],
    )


@router.get("/status", response_model=Status)
async def status(session: Session) -> Status:
    """Settings > Status (PRD O5): daily search, stuck searches, errors, slow routes, disk,
    spend and recent health alerts."""
    return await health.status(session)


@router.get("/capacity", response_model=Capacity)
async def capacity(session: Session, days: int = DEFAULT_DAYS) -> Capacity:
    """How long an application takes and how many fit in a browser-hour (IDEA-15), from
    the step durations already in log_event. The window cannot usefully exceed the 30 days
    those are kept."""
    if not 1 <= days <= 30:
        raise ApiError(422, "bad_window", "Ask for between 1 and 30 days")
    return await read_capacity(session, days)


@router.post("/health-check", status_code=202)
async def health_check_now() -> dict[str, bool]:
    """Run health-watch now: old log rows deleted, alerts that are due emailed."""
    await send("health/check-requested")
    return {"queued": True}


@router.post("/backup", status_code=202)
async def backup_now() -> dict[str, bool]:
    await send("backup/requested")
    return {"queued": True}


@router.post("/form-check", status_code=202)
async def form_check_now() -> dict[str, bool]:
    await send("form-check/requested")
    return {"queued": True}


@router.get("/form-check/{check_id}/screenshot")
async def form_check_screenshot(check_id: uuid.UUID, session: Session) -> FileResponse:
    row = await session.get(FormCheck, check_id)
    path = storage.served(row.screenshot_path if row else None)
    if path is None:
        raise ApiError(404, "not_found", "No screenshot for this form test")
    return FileResponse(path, media_type="image/png")
