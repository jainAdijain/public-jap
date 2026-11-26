"""The admin's view of the service (PRD S12): accounts, the sign-up switch and the invite list
(launch decision D4)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from schemas.profile import AiSource, Plan, Spend


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
    ai_source: AiSource = "none"  # own key, credit on JAP's key, or neither (D1)
    may_automatic: bool = False  # the admin lets it use Automatic mode (D2)


class LimitsIn(BaseModel):
    """The admin's numbers for one account. None puts that one back on the plan's."""

    ai_usd: float | None = Field(default=None, ge=0, le=1000)
    boards: int | None = Field(default=None, ge=0, le=10000)
    applications_per_day: int | None = Field(default=None, ge=0, le=100)


class Accounts(BaseModel):
    accounts: list[AccountOut]
    sign_up_paused: bool
    # Addresses that may sign up while sign-up is paused (D4); each leaves once it is used.
    invites: list[str] = []
    spend_total: Spend  # every account and the shared job postings together
    spend_shared: Spend  # reading shared job postings, against the service's budget


class SignUpSwitch(BaseModel):
    paused: bool


class AutomaticSwitch(BaseModel):
    allowed: bool


class InviteIn(BaseModel):
    email: EmailStr
