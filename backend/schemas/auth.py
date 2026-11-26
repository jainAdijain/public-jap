"""Sign-in and account (PRD S1 to S9, U21)."""

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class SignUp(Credentials):
    # The Turnstile token from the page, when the check is on (auth/turnstile.py).
    turnstile: str | None = Field(default=None, max_length=4000)
    # The terms and privacy tick, with the version of the notice the page showed (S14).
    accept_terms: bool = False
    terms_version: str = Field(default="", max_length=40)


class VerifyEmail(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class Code(BaseModel):
    # A 6-digit app code or a recovery code (xxxxx-xxxxx).
    code: str = Field(min_length=6, max_length=20)


class Forgot(BaseModel):
    email: EmailStr
    turnstile: str | None = Field(default=None, max_length=4000)


class Resend(BaseModel):
    email: EmailStr


class Reset(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class Password(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class AcceptTerms(BaseModel):
    """The tick, from an account made before this notice (S14)."""

    terms_version: str = Field(min_length=1, max_length=40)


class UserOut(BaseModel):
    id: UUID
    email: str
    is_admin: bool
    two_step: bool
    recovery_codes_left: int
    # False while this account has not agreed to the notice as it stands (S14): the app
    # shows the agreement screen before anything else, and the API refuses the rest of
    # /api until it is given. Required, so a caller cannot leave it out and read as agreed.
    terms_accepted: bool
    # How this account can sign in (S16). An account made through Google has no password
    # until it sets one, and Google cannot be disconnected while that is so.
    google_connected: bool = False
    has_password: bool = True


class AuthState(BaseModel):
    """What the sign-in screen shows: create an account, sign in, or the code step."""

    has_accounts: bool
    sign_up_open: bool  # False while the admin has sign-up paused (S12)
    # Paused, but the admin has invited someone (D4): the form stays, for the invited only.
    sign_up_by_invite: bool = False
    user: UserOut | None
    two_step_pending: bool
    turnstile_site_key: str = ""  # set: the page shows the "prove you're human" check
    terms_version: str = ""  # the notice the sign-up tick agrees to (S14)
    google: bool = False  # the GOOGLE_ settings are filled in, so the button is shown (S16)


class SignInResult(BaseModel):
    signed_in: bool
    two_step: bool
    user: UserOut | None
    # A new account: no session yet, the page says Check your email (S10).
    verify_email: bool = False


class TwoStepSetup(BaseModel):
    secret: str
    uri: str
    qr_svg: str


class RecoveryCodes(BaseModel):
    recovery_codes: list[str]
