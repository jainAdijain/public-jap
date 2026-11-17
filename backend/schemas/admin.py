"""The admin's view of the service (PRD S12): accounts and the sign-up switch."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from schemas.profile import Spend


class AccountOut(BaseModel):
    id: UUID
    email: str
    is_admin: bool
    joined_at: datetime
    last_sign_in_at: datetime | None
    # active, unverified (the emailed link not opened yet) or off (turned off by the admin)
    state: str
    spend: Spend  # this month, against the account's own allowance


class Accounts(BaseModel):
    accounts: list[AccountOut]
    sign_up_paused: bool
    spend_total: Spend  # every account and the shared job postings together
    spend_shared: Spend  # reading shared job postings, against the service's budget


class SignUpSwitch(BaseModel):
    paused: bool
