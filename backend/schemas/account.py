"""Export my data, and delete my account (PRD S15)."""

from pydantic import BaseModel, Field


class DeleteAccount(BaseModel):
    """What deleting asks for: the password, the code when two-step sign-in is on, and the
    account's own address typed out, so it cannot be done by one mis-aimed click."""

    password: str = Field(min_length=1, max_length=256)
    # The 6-digit app code or a recovery code, when two-step sign-in is on.
    code: str = Field(default="", max_length=20)
    confirm_email: str = Field(min_length=1, max_length=320)


class Deleted(BaseModel):
    deleted: bool = True
