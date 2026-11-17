"""The admin's view of the service (PRD S12): accounts and the sign-up switch."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from schemas.profile import Plan, Spend


class AccountOut(BaseModel):
    id: UUID
    email: str
    is_admin: bool
    joined_at: datetime
    last_sign_in_at: datetime | None
    # active, unverified (the emailed link not opened yet) or off (turned off by the admin)
    state: str
    spend: Spend  # this month, against the account's own allowance
    plan: Plan  # its limits, with custom=True when the admin set any of them


class LimitsIn(BaseModel):
    """The admin's numbers for one account. None puts that one back on the plan's."""

    ai_usd: float | None = Field(default=None, ge=0, le=1000)
    boards: int | None = Field(default=None, ge=0, le=10000)
    applications_per_day: int | None = Field(default=None, ge=0, le=100)


class Accounts(BaseModel):
    accounts: list[AccountOut]
    sign_up_paused: bool
    spend_total: Spend  # every account and the shared job postings together
    spend_shared: Spend  # reading shared job postings, against the service's budget


class SignUpSwitch(BaseModel):
    paused: bool
