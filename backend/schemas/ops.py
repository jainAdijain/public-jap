from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class BackupOut(BaseModel):
    file: str
    at: datetime
    bytes: int


class FormCheckOut(BaseModel):
    id: UUID
    ats: str
    ok: bool
    fields: int
    required: int
    url: str | None
    error: str | None
    has_screenshot: bool
    checked_at: datetime


class Ops(BaseModel):
    """The Hardening part of the Status card: backups and the weekly form test."""

    last_backup: BackupOut | None
    backups: int
    backup_today: bool
    form_checks: list[FormCheckOut]


class StepTime(BaseModel):
    """One apply step, over the window: how often it ran and how long it took."""

    step: str
    n: int
    median_ms: int
    p90_ms: int
    worst_ms: int


class SiteRow(BaseModel):
    """What one job site did with the submissions sent to it (IDEA-17). Rejections and
    codes climbing is what says the service is being read as a bot."""

    ats: str
    submissions: int
    confirmed: int
    rejected: int
    needs_code: int


class Capacity(BaseModel):
    """How long applying takes, and how many can run at once (IDEA-15).

    median_ms and the rest are per application: the total of its steps, which is how long
    it holds a worker. per_browser_hour is what one browser gets through in an hour.
    """

    days: int
    applications: int
    steps: list[StepTime]
    sites: list[SiteRow] = []
    median_ms: int | None
    p90_ms: int | None
    worst_ms: int | None
    per_browser_hour: float | None
