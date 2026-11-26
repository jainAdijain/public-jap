"""Sign-in, sign-up, password reset and two-step sign-in (PRD S1 to S9, S14, U21).

These routes are the only ones besides /api/health that answer without a session. The rest of
/api carries the AgreedUser dependency (main.py), which also wants the terms ticked; these do
not, because /auth/terms is how an older account gives that tick.

Sign-up is open (PRD 1.18 step 2: S1, S10). The first account is the admin and is signed in
at once. Every account after it gets an emailed link and has no session until the link is
opened; what the page hears is the same whether or not the address already had an account.
Sign-up, forgot password and "send the link again" are limited per address and per email
(auth/limits.py) and can ask for proof of a human (auth/turnstile.py), and the admin can
pause sign-up altogether (S12).
"""

import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ApiError
from auth import accounts, consent, limits, oidc, passwords, sessions, totp, turnstile
from auth.deps import CurrentSession, CurrentUser
from config import get_settings
from database.models import AppUser, PasswordReset, UserIdentity
from database.session import Session
from pipeline.notify import EmailNotSent, Mail
from pipeline.notify import send as send_mail
from schemas.auth import (
    AcceptTerms,
    AuthState,
    Code,
    Credentials,
    Forgot,
    Password,
    PasswordChange,
    RecoveryCodes,
    Resend,
    Reset,
    SignInResult,
    SignUp,
    TwoStepSetup,
    UserOut,
    VerifyEmail,
)

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("jap.auth")

MAX_FAILED = 5
LOCK_MINUTES = 15
RESET_MINUTES = 30
RESET_EVERY = timedelta(minutes=2)
WRONG = "Wrong email or password"
SIGN_UP_CLOSED = "Sign-up is paused for now. Try again later."
UNVERIFIED = "Confirm your email first: open the link JAP sent you."
ACCOUNT_OFF = "This account is turned off. Ask the JAP admin."
EXPIRED_LINK = "This link has expired or was already used. Ask for a new one."
SIGN_UP_LOCK = 7_342_001  # pg_advisory_xact_lock key: one sign-up at a time


async def user_out(session: AsyncSession, user: AppUser) -> UserOut:
    """Takes the session because what this account can sign in with (S16) is a row, not a
    column: reporting it from a default would be reporting a guess."""
    google = await session.scalar(
        select(UserIdentity.id).where(
            UserIdentity.user_id == user.id, UserIdentity.provider == "google"
        )
    )
    return UserOut(
        id=user.id,
        email=user.email,
        is_admin=user.is_admin,
        two_step=user.totp_secret is not None,
        recovery_codes_left=len(user.recovery_codes or []),
        terms_accepted=consent.accepted_terms(user),
        google_connected=google is not None,
        has_password=bool(user.password_hash),
    )


async def _by_email(session: AsyncSession, email: str) -> AppUser | None:
    return await session.scalar(select(AppUser).where(func.lower(AppUser.email) == email.lower()))


def _locked(user: AppUser) -> None:
    if user.locked_until is not None and user.locked_until > sessions.now():
        left = max(1, round((user.locked_until - sessions.now()).total_seconds() / 60))
        raise ApiError(429, "locked", f"Too many tries. Try again in {left} min.")


async def _failed(session: AsyncSession, user: AppUser) -> None:
    user.failed_sign_ins += 1
    if user.failed_sign_ins >= MAX_FAILED:
        user.failed_sign_ins = 0
        user.locked_until = sessions.now() + timedelta(minutes=LOCK_MINUTES)
    await session.commit()


def _new_password(password: str) -> None:
    problem = passwords.problem(password)
    if problem:
        raise ApiError(422, "weak_password", problem)


def _ticked(accepted: bool, version: str) -> None:
    """The terms and privacy tick (S14). A tick against an older notice does not count:
    the page is told the current version and sends back the one it displayed."""
    if not accepted or version != consent.TERMS_VERSION:
        raise ApiError(422, "terms_not_accepted", consent.NEEDS_TERMS)


@router.get("/state", response_model=AuthState)
async def state(request: Request, session: Session) -> AuthState:
    has_accounts = await accounts.any_account(session)
    row = await sessions.find(session, request, sessions.SIGNED_IN)
    user = await session.get(AppUser, row.user_id) if row else None
    pending = await sessions.find(session, request, sessions.TWO_STEP)
    paused = has_accounts and await accounts.sign_up_paused(session)
    return AuthState(
        has_accounts=has_accounts,
        sign_up_open=not paused,
        sign_up_by_invite=paused and bool(await accounts.invites(session)),
        user=await user_out(session, user) if user else None,
        two_step_pending=user is None and pending is not None,
        turnstile_site_key=get_settings().turnstile_site_key,
        terms_version=consent.TERMS_VERSION,
        google=get_settings().google_ready,
    )


async def _send(mail: Mail | None, what: str) -> None:
    if mail is None:
        return
    try:
        await send_mail(mail)
    except EmailNotSent as exc:
        log.warning("%s email not sent: %s", what, exc)


async def _first_account(session: AsyncSession, body: SignUp) -> AppUser:
    """The admin (S1): the first account, or the one that takes over the placeholder owner
    of data that was there before any account."""
    allowed = get_settings().jap_admin_email.strip()
    if allowed and allowed.lower() != body.email.lower():
        raise ApiError(403, "not_admin_email", "This address can't create the first JAP account")
    _new_password(body.password)
    user = await accounts.unclaimed(session)
    if user is None:
        user = AppUser(email=body.email, password_hash="", is_admin=True)
        session.add(user)
    user.email = body.email
    user.password_hash = await passwords.hash_password(body.password)
    user.is_admin = True
    user.email_verified_at = sessions.now()
    user.password_changed_at = sessions.now()
    consent.accept_terms(user, sessions.now())
    await session.flush()
    return user


@router.post("/sign-up", response_model=SignInResult, status_code=201)
async def sign_up(
    body: SignUp, request: Request, response: Response, session: Session
) -> SignInResult:
    """Create an account (S1). The first one is the admin and is signed in at once. Any
    other gets an emailed link (S10) and the same answer whatever the address: a new
    account, one still waiting for its link (which is sent again), or one that exists."""
    await limits.check(session, request, "sign-up", body.email)
    await turnstile.check(body.turnstile, limits.client_ip(request))
    _ticked(body.accept_terms, body.terms_version)
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SIGN_UP_LOCK})
    if not await accounts.any_account(session):
        user = await _first_account(session, body)
        await accounts.start_workspace(session, user)
        user.last_sign_in_at = sessions.now()
        await sessions.start(session, user, sessions.SIGNED_IN, request, response)
        await session.commit()
        return SignInResult(signed_in=True, two_step=False, user=await user_out(session, user))
    if not await accounts.may_sign_up(session, body.email):
        raise ApiError(403, "sign_up_closed", SIGN_UP_CLOSED)
    _new_password(body.password)
    existing = await _by_email(session, body.email)
    mail: Mail | None = None
    if existing is None:
        user = AppUser(email=body.email, password_hash=await passwords.hash_password(body.password))
        consent.accept_terms(user, sessions.now())
        session.add(user)
        await session.flush()
        mail = await accounts.verification_mail(session, user, force=True)
        await accounts.start_workspace(session, user)
        await accounts.uninvite(session, body.email)  # used: an invite admits one account
    elif existing.email_verified_at is None and existing.disabled_at is None:
        # Still waiting for its link: the tick just given counts for it too (S14), so the
        # account is not asked a second time the moment the link is opened.
        consent.accept_terms(existing, sessions.now())
        mail = await accounts.verification_mail(session, existing)
    await session.commit()
    await _send(mail, "Verification")
    return SignInResult(signed_in=False, two_step=False, user=None, verify_email=True)


@router.post("/verify", response_model=SignInResult)
async def verify_email(
    body: VerifyEmail, request: Request, response: Response, session: Session
) -> SignInResult:
    """Open the emailed link (S10): the address is confirmed and this browser is signed in."""
    user = await accounts.verify(session, body.token)
    if user is None:
        raise ApiError(400, "verify_link_expired", EXPIRED_LINK)
    if user.disabled_at is not None:
        raise ApiError(403, "account_off", ACCOUNT_OFF)
    user.last_sign_in_at = sessions.now()
    await sessions.start(session, user, sessions.SIGNED_IN, request, response)
    await session.commit()
    return SignInResult(signed_in=True, two_step=False, user=await user_out(session, user))


@router.post("/verify/resend", status_code=202)
async def resend_verification(body: Resend, request: Request, session: Session) -> dict[str, bool]:
    """Send again (S10). The answer is the same for any address; at most one email goes out
    every 2 minutes per account."""
    await limits.check(session, request, "verify-resend", body.email)
    user = await _by_email(session, body.email)
    mail: Mail | None = None
    if user is not None and user.email_verified_at is None and user.disabled_at is None:
        mail = await accounts.verification_mail(session, user)
        await session.commit()
    await _send(mail, "Verification")
    return {"queued": True}


@router.post("/sign-in", response_model=SignInResult)
async def sign_in(
    body: Credentials, request: Request, response: Response, session: Session
) -> SignInResult:
    user = await _by_email(session, body.email)
    if user is None:
        await passwords.verify_password(body.password, passwords.DUMMY_HASH)
        raise ApiError(401, "wrong_credentials", WRONG)
    _locked(user)
    if not await passwords.verify_password(body.password, user.password_hash):
        await _failed(session, user)
        raise ApiError(401, "wrong_credentials", WRONG)
    if user.disabled_at is not None:
        raise ApiError(403, "account_off", ACCOUNT_OFF)
    if user.email_verified_at is None:
        raise ApiError(403, "email_unverified", UNVERIFIED)
    await sessions.sweep(session)
    if user.totp_secret is not None:
        await sessions.start(session, user, sessions.TWO_STEP, request, response)
        await session.commit()
        return SignInResult(signed_in=False, two_step=True, user=None)
    user.failed_sign_ins = 0
    user.locked_until = None
    user.last_sign_in_at = sessions.now()
    await sessions.start(session, user, sessions.SIGNED_IN, request, response)
    await session.commit()
    return SignInResult(signed_in=True, two_step=False, user=await user_out(session, user))


def use_code(user: AppUser, code: str) -> bool:
    """True when code is the app's code now or an unused recovery code (used up here)."""
    assert user.totp_secret is not None
    step = totp.matching_step(user.totp_secret, code, user.totp_last_step)
    if step is not None:
        user.totp_last_step = step
        return True
    hashed = totp.recovery_hash(code)
    codes = list(user.recovery_codes or [])
    if hashed in codes:
        codes.remove(hashed)
        user.recovery_codes = codes
        return True
    return False


@router.post("/sign-in/code", response_model=SignInResult)
async def sign_in_code(
    body: Code, request: Request, response: Response, session: Session
) -> SignInResult:
    """The second step (S3): the authenticator code, or a recovery code."""
    pending = await sessions.find(session, request, sessions.TWO_STEP)
    if pending is None:
        raise ApiError(401, "two_step_expired", "Sign-in timed out. Enter your password again.")
    user = await session.get(AppUser, pending.user_id)
    if user is None or user.totp_secret is None:
        raise ApiError(401, "two_step_expired", "Sign-in timed out. Enter your password again.")
    _locked(user)
    if not use_code(user, body.code):
        pending.code_tries += 1
        if pending.code_tries >= sessions.TWO_STEP_TRIES:
            await session.delete(pending)
            sessions.clear_cookie(response, sessions.TWO_STEP)
        await _failed(session, user)
        raise ApiError(401, "wrong_code", "That code didn't work")
    await session.delete(pending)
    sessions.clear_cookie(response, sessions.TWO_STEP)
    user.failed_sign_ins = 0
    user.locked_until = None
    user.last_sign_in_at = sessions.now()
    await sessions.start(session, user, sessions.SIGNED_IN, request, response)
    await session.commit()
    return SignInResult(signed_in=True, two_step=False, user=await user_out(session, user))


@router.post("/sign-out", status_code=204)
async def sign_out(request: Request, response: Response, session: Session) -> None:
    for stage in (sessions.SIGNED_IN, sessions.TWO_STEP):
        row = await sessions.find(session, request, stage)
        if row is not None:
            await session.delete(row)
        sessions.clear_cookie(response, stage)
    await session.commit()


@router.post("/sign-out-everywhere", status_code=204)
async def sign_out_everywhere(user: CurrentUser, response: Response, session: Session) -> None:
    await sessions.end_all(session, user.id)
    await session.commit()
    sessions.clear_cookie(response, sessions.SIGNED_IN)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser, session: Session) -> UserOut:
    return await user_out(session, user)


@router.post("/terms", response_model=UserOut)
async def accept_terms(body: AcceptTerms, user: CurrentUser, session: Session) -> UserOut:
    """Agree to the terms and the privacy notice (S14), from an account made before this
    notice. New accounts tick the same box at sign-up."""
    _ticked(True, body.terms_version)
    consent.accept_terms(user, sessions.now())
    await session.commit()
    return await user_out(session, user)


@router.post("/forgot", status_code=202)
async def forgot(body: Forgot, request: Request, session: Session) -> dict[str, bool]:
    """Email a reset link to the account's address (S4). The answer is the same for any address."""
    await limits.check(session, request, "forgot", body.email)
    await turnstile.check(body.turnstile, limits.client_ip(request))
    user = await _by_email(session, body.email)
    if user is None:
        return {"queued": True}
    last = await session.scalar(
        select(func.max(PasswordReset.created_at)).where(PasswordReset.user_id == user.id)
    )
    if last is not None and sessions.now() - last < RESET_EVERY:
        return {"queued": True}
    token = secrets.token_urlsafe(32)
    session.add(
        PasswordReset(
            user_id=user.id,
            token_hash=sessions.token_hash(token),
            expires_at=sessions.now() + timedelta(minutes=RESET_MINUTES),
        )
    )
    await session.commit()
    link = f"{get_settings().jap_ui_url.rstrip('/')}/?reset={token}"
    mail = Mail(
        to=user.email,
        subject="Reset your JAP password",
        body=(
            "Someone (hopefully you) asked to reset the password for your JAP account.\n\n"
            f"Set a new password here within {RESET_MINUTES} minutes:\n{link}\n\n"
            "The link works once. Setting a new password signs out every browser.\n"
            "If you didn't ask for this, ignore this email; your password stays the same.\n"
        ),
    )
    await _send(mail, "Password reset")
    return {"queued": True}


@router.post("/reset", status_code=204)
async def reset(body: Reset, session: Session) -> None:
    row = await session.scalar(
        select(PasswordReset).where(PasswordReset.token_hash == sessions.token_hash(body.token))
    )
    if row is None or row.used_at is not None or row.expires_at <= sessions.now():
        raise ApiError(400, "reset_link_expired", EXPIRED_LINK)
    _new_password(body.password)
    user = await session.get(AppUser, row.user_id)
    assert user is not None
    row.used_at = sessions.now()
    user.password_hash = await passwords.hash_password(body.password)
    user.password_changed_at = sessions.now()
    user.failed_sign_ins = 0
    user.locked_until = None
    await sessions.end_all(session, user.id)
    await session.commit()


async def check_password(session: AsyncSession, user: AppUser, password: str) -> None:
    """The account's own password, with the same lockout as signing in. Shared with
    api/account.py, which asks for it before deleting everything (S15)."""
    _locked(user)
    if not await passwords.verify_password(password, user.password_hash):
        await _failed(session, user)
        raise ApiError(403, "wrong_password", "Your current password is not right")


@router.post("/password", status_code=204)
async def change_password(
    body: PasswordChange, user: CurrentUser, current: CurrentSession, session: Session
) -> None:
    """Change the password (S7). Every other browser is signed out."""
    await check_password(session, user, body.current_password)
    _new_password(body.new_password)
    user.password_hash = await passwords.hash_password(body.new_password)
    user.password_changed_at = sessions.now()
    await sessions.end_all(session, user.id, keep=current)
    await session.commit()


@router.post("/two-step/start", response_model=TwoStepSetup)
async def two_step_start(user: CurrentUser, session: Session) -> TwoStepSetup:
    """A new secret to scan; two-step is on only after /two-step/confirm."""
    if user.totp_secret is not None:
        raise ApiError(409, "two_step_on", "Two-step sign-in is already on")
    secret = totp.new_secret()
    user.totp_pending_secret = secret
    await session.commit()
    uri = totp.uri(secret, user.email)
    return TwoStepSetup(secret=secret, uri=uri, qr_svg=totp.qr_svg(uri))


@router.post("/two-step/confirm", response_model=RecoveryCodes)
async def two_step_confirm(body: Code, user: CurrentUser, session: Session) -> RecoveryCodes:
    if user.totp_secret is not None:
        raise ApiError(409, "two_step_on", "Two-step sign-in is already on")
    if user.totp_pending_secret is None:
        raise ApiError(409, "no_setup", "Start setting up two-step sign-in first")
    step = totp.matching_step(user.totp_pending_secret, body.code, None)
    if step is None:
        raise ApiError(422, "wrong_code", "That code didn't work. Check the time on your phone.")
    codes = totp.new_recovery_codes()
    user.totp_secret = user.totp_pending_secret
    user.totp_pending_secret = None
    user.totp_last_step = step
    user.recovery_codes = [totp.recovery_hash(c) for c in codes]
    await session.commit()
    return RecoveryCodes(recovery_codes=codes)


@router.post("/two-step/off", status_code=204)
async def two_step_off(body: Password, user: CurrentUser, session: Session) -> None:
    await check_password(session, user, body.password)
    user.totp_secret = None
    user.totp_pending_secret = None
    user.totp_last_step = None
    user.recovery_codes = []
    await session.commit()


@router.post("/two-step/recovery-codes", response_model=RecoveryCodes)
async def new_recovery_codes(body: Password, user: CurrentUser, session: Session) -> RecoveryCodes:
    """New recovery codes; the old ones stop working."""
    await check_password(session, user, body.password)
    if user.totp_secret is None:
        raise ApiError(409, "two_step_off", "Two-step sign-in is off")
    codes = totp.new_recovery_codes()
    user.recovery_codes = [totp.recovery_hash(c) for c in codes]
    await session.commit()
    return RecoveryCodes(recovery_codes=codes)


# Sign in with Google (S16) ---------------------------------------------------------------


def _google_on() -> None:
    """Without the three GOOGLE_ settings there is no such feature: Not Found, not an
    error, so a JAP without it looks like a JAP that never had it."""
    if not get_settings().google_ready:
        raise ApiError(404, "not_found", "Not Found")


def _back(outcome: str) -> RedirectResponse:
    """The browser comes back from Google to an API route, so every ending is a redirect to
    the UI. The UI reads ?google= and says what happened."""
    ui = get_settings().jap_ui_url.rstrip("/")
    return RedirectResponse(f"{ui}/?google={outcome}", status_code=303)


@router.get("/google/start")
async def google_start() -> RedirectResponse:
    """Off to Google. The state, nonce and PKCE verifier go in one HttpOnly cookie and
    nowhere else, so a callback that cannot show them did not start here."""
    _google_on()
    state, nonce, verifier = (oidc.new_verifier() for _ in range(3))
    response = RedirectResponse(await oidc.start_url(state, nonce, verifier), status_code=303)
    settings = get_settings()
    response.set_cookie(
        sessions.cookie_name(sessions.OAUTH),
        f"{state}.{nonce}.{verifier}",
        max_age=oidc.START_SECONDS,
        path="/",
        secure=settings.jap_cookie_secure,
        httponly=True,
        # lax, not strict: coming back from Google is a cross-site navigation, and strict
        # would withhold the cookie, so every sign-in would fail its own state check.
        samesite="lax",
    )
    return response


async def _google_user(
    session: AsyncSession, who: oidc.Identity, signed_in: AppUser | None
) -> AppUser:
    """The account this Google identity signs in as: the one already linked, the one being
    linked now, or a new one."""
    linked = await session.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == "google", UserIdentity.subject == who.subject
        )
    )
    if linked is not None:
        if signed_in is not None and linked.user_id != signed_in.id:
            raise ApiError(409, "google_taken", "That Google account is on another JAP account")
        linked.email = who.email  # what they said this time; never looked up by
        user = await session.get(AppUser, linked.user_id)
        if user is None:
            raise ApiError(403, "account_off", ACCOUNT_OFF)
        return user
    if signed_in is not None:  # Connect, from Settings: they already proved this account
        session.add(
            UserIdentity(
                user_id=signed_in.id, provider="google", subject=who.subject, email=who.email
            )
        )
        return signed_in
    existing = await _by_email(session, who.email)
    if existing is not None:
        # Linking on the address alone would hand the account to anyone who can make a
        # Google account carrying it, so Google has to vouch for the address first.
        if not who.email_verified:
            raise ApiError(
                403,
                "email_unverified",
                "Sign in with your password and connect Google from Settings",
            )
        session.add(
            UserIdentity(
                user_id=existing.id, provider="google", subject=who.subject, email=who.email
            )
        )
        return existing
    first = not await accounts.any_account(session)
    # An invite is for an address (D4), so Google has to vouch for it before it counts.
    if not first and not (
        await accounts.may_sign_up(session, who.email)
        and (who.email_verified or not await accounts.sign_up_paused(session))
    ):
        raise ApiError(403, "sign_up_closed", SIGN_UP_CLOSED)
    # A new account with no password at all: Google is how it signs in (S16). It is not
    # asked for the terms here, having no session to be asked in, so it is made without
    # them and meets the same agreement screen every account does on its next request
    # (auth/deps.py: agreed_user), which it cannot get past without ticking.
    user = AppUser(
        email=who.email, password_hash="", is_admin=first, email_verified_at=sessions.now()
    )
    session.add(user)
    await session.flush()
    session.add(
        UserIdentity(user_id=user.id, provider="google", subject=who.subject, email=who.email)
    )
    await accounts.start_workspace(session, user)
    await accounts.uninvite(session, who.email)
    return user


@router.get("/google/callback")
async def google_callback(
    request: Request, response: Response, session: Session, code: str = "", state: str = ""
) -> RedirectResponse:
    """Back from Google: check the handshake, swap the code, then sign in, link or create."""
    _google_on()
    await limits.check(session, request, "google", None)
    cookie = request.cookies.get(sessions.cookie_name(sessions.OAUTH), "")
    parts = cookie.split(".")
    if not code or len(parts) != 3 or not secrets.compare_digest(parts[0], state):
        return _back("expired")  # not ours, or they sat on Google's page too long
    _, nonce, verifier = parts
    row = await sessions.find(session, request, sessions.SIGNED_IN)
    signed_in = await session.get(AppUser, row.user_id) if row else None
    try:
        who = await oidc.exchange(code, verifier, nonce)
    except oidc.OidcError as exc:
        log.warning("Google sign-in refused: %s", exc)
        return _back("failed")
    try:
        user = await _google_user(session, who, signed_in)
    except ApiError as exc:
        await session.rollback()
        return _back(exc.code)
    if user.disabled_at is not None:
        await session.rollback()
        return _back("account_off")
    if signed_in is None:
        user.last_sign_in_at = sessions.now()
        await sessions.start(session, user, sessions.SIGNED_IN, request, response)
    await session.commit()
    out = _back("connected" if signed_in is not None else "signed_in")
    for header, value in response.raw_headers:  # the session cookie sessions.start just set
        if header.lower() == b"set-cookie":
            out.raw_headers.append((header, value))
    out.delete_cookie(
        sessions.cookie_name(sessions.OAUTH),
        path="/",
        secure=get_settings().jap_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return out


@router.post("/google/disconnect", response_model=UserOut)
async def google_disconnect(user: CurrentUser, session: Session) -> UserOut:
    """Stop signing in with Google. Refused while there is no password to sign in with
    instead, which would lock the account out of itself."""
    _google_on()
    if not user.password_hash:
        raise ApiError(409, "no_password", "Set a password first, or you could not sign in again")
    await session.execute(
        delete(UserIdentity).where(
            UserIdentity.user_id == user.id, UserIdentity.provider == "google"
        )
    )
    await session.commit()
    return await user_out(session, user)
