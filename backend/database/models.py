"""SQLAlchemy mappings for the tables the app writes so far.

The schema itself lives in migrations/versions (0001_initial); these classes only map it.
Every table the app writes has its class here.
"""

import uuid
from datetime import datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.tenant import USER_ID_SQL

ATS = ENUM("greenhouse", "lever", "ashby", name="ats", create_type=False)
WORK_MODE = ENUM("remote", "hybrid", "onsite", name="work_mode", create_type=False)
APPLY_MODE = ENUM("review", "automatic", name="apply_mode", create_type=False)
POSTING_STATE = ENUM("open", "closed", name="posting_state", create_type=False)
RUN_TRIGGER = ENUM(
    "resume_updated", "manual", "scheduled", "profile_saved", name="run_trigger", create_type=False
)
RUN_MODE = ENUM("find_only", "find_and_apply", name="run_mode", create_type=False)
LISTING_STATUS = ENUM(
    "not_applied", "ready_for_review", "applying", "applied", "needs_input", "failed",
    "manual_apply", "skipped", "closed", name="listing_status", create_type=False,
)  # fmt: skip
APPLICATION_STATUS = ENUM(
    "preparing", "ready_for_review", "applying", "applied", "needs_input", "failed",
    "cancelled", name="application_status", create_type=False,
)  # fmt: skip
RUN_STATUS = ENUM("queued", "running", "succeeded", "failed", name="run_status", create_type=False)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )


def _owner(**kw: Any) -> Mapped[uuid.UUID]:
    """The account a private row belongs to (PRD S7). Left unset, the database fills it
    from the transaction's app.user_id (database/tenant.py), and refuses the row without one."""
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), server_default=text(USER_ID_SQL), **kw
    )


class Profile(Timestamps, Base):
    __tablename__ = "profile"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = _owner(unique=True)
    tz: Mapped[str | None] = mapped_column(Text)  # None: JAP_TZ
    full_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    links: Mapped[list[dict[str, str]]] = mapped_column(JSONB, server_default="[]")
    target_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    must_have_skills: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    nice_to_have_skills: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    locations: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    remote_regions: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    remote_any: Mapped[bool] = mapped_column(Boolean, server_default="false")  # all remote jobs
    match_rule: Mapped[list[list[str]]] = mapped_column(
        JSONB, server_default='[["location", "role"]]'
    )
    work_modes: Mapped[list[str]] = mapped_column(
        ARRAY(WORK_MODE), server_default="{remote,hybrid,onsite}"
    )
    excluded_companies: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    excluded_title_words: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    tone_note: Mapped[str | None] = mapped_column(Text)
    apply_mode: Mapped[str] = mapped_column(APPLY_MODE, server_default="review")
    daily_run: Mapped[bool] = mapped_column(Boolean, server_default="false")
    daily_run_at: Mapped[time] = mapped_column(Time, server_default="06:00")
    daily_run_last_at: Mapped[datetime | None]
    daily_cap: Mapped[int] = mapped_column(Integer, server_default="20")
    # IDEA-17: this account's sending stopped, without turning the account off or taking
    # back its consent. The global switch is JAP_SUBMIT_ENABLED.
    submit_paused: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # F6: Automatic mode applies only at or above this share of my required skills.
    auto_apply_min_skills: Mapped[int] = mapped_column(SmallInteger, server_default="60")
    spend_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))  # None: .env cap
    # Consent (PRD S14): JAP may apply in my name, and may do it without asking (Automatic).
    # Null means not agreed, and nothing is submitted.
    submit_opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResumeVersion(Timestamps, Base):
    __tablename__ = "resume_version"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = _owner()
    file_path: Mapped[str] = mapped_column(Text)
    file_sha256: Mapped[str] = mapped_column(Text)
    original_name: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    parse_error: Mapped[str | None] = mapped_column(Text)
    accepted_at: Mapped[datetime | None]
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="false")
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now())


class WatchedBoard(Timestamps, Base):
    __tablename__ = "watched_board"

    id: Mapped[uuid.UUID] = _id()
    ats: Mapped[str] = mapped_column(ATS)
    board_token: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    origin: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    last_checked_at: Mapped[datetime | None]
    last_ok: Mapped[bool | None]


class Company(Timestamps, Base):
    """Canonical company: one row however many boards and spellings it has."""

    __tablename__ = "company"

    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(Text)
    canonical_name: Mapped[str] = mapped_column(Text, unique=True)
    careers_domain: Mapped[str | None] = mapped_column(Text, unique=True)


class CompanyAlias(Timestamps, Base):
    __tablename__ = "company_alias"

    alias: Mapped[str] = mapped_column(Text, primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    origin: Mapped[str] = mapped_column(Text)  # auto or user


class Listing(Timestamps, Base):
    """One company plus one role (D1 to D4); what the dashboard shows."""

    __tablename__ = "listing"

    id: Mapped[uuid.UUID] = _id()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    canonical_role: Mapped[str] = mapped_column(Text)
    title_display: Mapped[str] = mapped_column(Text)
    locations: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    work_mode: Mapped[str | None] = mapped_column(WORK_MODE)
    remote_regions: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    skills: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    seniority: Mapped[str | None] = mapped_column(Text)
    primary_posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    first_posted_at: Mapped[datetime | None]
    last_posted_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]  # when its last open posting closed; None while open


class UserListing(Timestamps, Base):
    """What one listing is to one user (PRD S7): whether it matches their profile, how it
    fits their skills, and where their application stands. The listing itself holds only
    facts about the job. A row exists only for a listing that matches or has a status."""

    __tablename__ = "user_listing"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id"),
        primary_key=True,
        server_default=text(USER_ID_SQL),
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("listing.id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(LISTING_STATUS, server_default="not_applied")
    matched: Mapped[list[str] | None] = mapped_column(ARRAY(Text))  # null = not listed (F3)
    matched_role: Mapped[str | None] = mapped_column(Text)  # target role it counts under (U2)
    # Skills match (F5), written with matched: my skills the job asks for, and the share of
    # my required ones (null = unknown: the job states no skills, or I have none required).
    required_hit: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    nice_hit: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    required_pct: Mapped[int | None] = mapped_column(SmallInteger)
    applied_at: Mapped[datetime | None]


class BoardFollow(Timestamps, Base):
    """A company one user follows (I8, PRD S7). The board itself is shared and is fetched
    while at least one follow of it is active (watched_board.active)."""

    __tablename__ = "board_follow"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id"),
        primary_key=True,
        server_default=text(USER_ID_SQL),
    )
    board_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("watched_board.id"), primary_key=True
    )
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class DedupOverride(Timestamps, Base):
    """A merge or split I chose (D5), remembered by posting so it survives later runs."""

    __tablename__ = "dedup_override"

    id: Mapped[uuid.UUID] = _id()
    kind: Mapped[str] = mapped_column(Text)  # merge or split
    posting_source: Mapped[str] = mapped_column(ATS)
    posting_external_id: Mapped[str] = mapped_column(Text)
    listing_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class PossibleDuplicate(Timestamps, Base):
    """A borderline pair (Near match) that the extract model merges or keeps apart."""

    __tablename__ = "possible_duplicate"

    id: Mapped[uuid.UUID] = _id()
    kind: Mapped[str] = mapped_column(Text)  # company or listing
    a_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    b_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB)
    decision: Mapped[str] = mapped_column(Text, server_default="open")


class Posting(Timestamps, Base):
    """One job found on one board (I2, I5, I6)."""

    __tablename__ = "posting"

    id: Mapped[uuid.UUID] = _id()
    source: Mapped[str] = mapped_column(ATS)
    external_id: Mapped[str] = mapped_column(Text)
    watched_board_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    listing_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    company_name_raw: Mapped[str] = mapped_column(Text)
    title_raw: Mapped[str] = mapped_column(Text)
    canonical_role: Mapped[str | None] = mapped_column(Text)
    requisition_id: Mapped[str | None] = mapped_column(Text)
    locations: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    department: Mapped[str | None] = mapped_column(Text)
    employment_type: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None]
    posting_url: Mapped[str] = mapped_column(Text)
    apply_url: Mapped[str] = mapped_column(Text)
    description_html: Mapped[str] = mapped_column(Text)
    description_text: Mapped[str] = mapped_column(Text)
    description_hash: Mapped[str] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extracted_hash: Mapped[str | None] = mapped_column(Text)
    # embedding (vector(768)) is written and read with plain SQL in pipeline/enrich.py.
    embedded_hash: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(POSTING_STATE, server_default="open")
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    closed_at: Mapped[datetime | None]
    # Why it is closed (I10): gone_from_board or unfollowed. Null while open.
    close_reason: Mapped[str | None] = mapped_column(Text)


class Run(Timestamps, Base):
    """One pipeline run (T4, T5)."""

    __tablename__ = "run"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = _owner()
    trigger: Mapped[str] = mapped_column(RUN_TRIGGER)
    mode: Mapped[str] = mapped_column(RUN_MODE)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(RUN_STATUS, server_default="queued")
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    inngest_run_id: Mapped[str | None] = mapped_column(Text)
    alert_sent_at: Mapped[datetime | None]


class Application(Timestamps, Base):
    """One apply attempt on a listing (Apply flow, A2). Frozen once Applied (a trigger)."""

    __tablename__ = "application"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = _owner()
    listing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    posting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(APPLICATION_STATUS, server_default="preparing")
    mode: Mapped[str] = mapped_column(APPLY_MODE)
    resume_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    jd_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    captured_at: Mapped[datetime | None]
    form_fields: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    answers: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cover_letter_text: Mapped[str | None] = mapped_column(Text)
    cover_letter_path: Mapped[str | None] = mapped_column(Text)
    claim_check: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    pending_questions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    screenshot_path: Mapped[str | None] = mapped_column(Text)
    trace_path: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    submit_started_at: Mapped[datetime | None]
    submitted_at: Mapped[datetime | None]
    email_sent_at: Mapped[datetime | None]
    # A5, IDEA-16: when the site asked for the code it emailed, and the code itself while
    # the form waits for it. Cleared the moment the form takes it, and never logged.
    code_wanted_at: Mapped[datetime | None]
    security_code: Mapped[str | None] = mapped_column(Text)
    review_reason: Mapped[str | None] = mapped_column(Text)  # why Automatic mode still waits
    alert_sent_at: Mapped[datetime | None]
    inngest_run_id: Mapped[str | None] = mapped_column(Text)


class AnswerBank(Timestamps, Base):
    __tablename__ = "answer_bank"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = _owner()
    question_pattern: Mapped[str] = mapped_column(Text)
    question_norm: Mapped[str] = mapped_column(Text)  # unique per user
    answer: Mapped[str] = mapped_column(Text)
    last_used_at: Mapped[datetime | None]


class SkillAlias(Timestamps, Base):
    __tablename__ = "skill_alias"

    alias: Mapped[str] = mapped_column(Text, primary_key=True)
    canonical: Mapped[str] = mapped_column(Text)


class LlmCall(Timestamps, Base):
    __tablename__ = "llm_call"

    id: Mapped[uuid.UUID] = _id()
    purpose: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_sha256: Mapped[str] = mapped_column(Text)
    blocked: Mapped[bool] = mapped_column(Boolean, server_default="false")
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    application_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Whose allowance paid (PRD S12): None and system for reading shared job postings.
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    billed_to: Mapped[str] = mapped_column(Text, server_default="system")


class UserSecret(Timestamps, Base):
    """One account's own API key, sealed (IDEA-19). The key is never here in the clear:
    llm/secrets.py opens it with the master key from the environment."""

    __tablename__ = "user_secret"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True, server_default="openrouter")
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    # The last four characters, so a person can tell which key is saved.
    hint: Mapped[str] = mapped_column(Text, server_default="")


class FormCheck(Timestamps, Base):
    """One weekly live form test: a real open posting's form read, nothing submitted."""

    __tablename__ = "form_check"

    id: Mapped[uuid.UUID] = _id()
    ats: Mapped[str] = mapped_column(ATS)
    posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    url: Mapped[str | None] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column(Boolean)
    fields: Mapped[int] = mapped_column(Integer, server_default="0")
    required: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    screenshot_path: Mapped[str | None] = mapped_column(Text)


class AppUser(Timestamps, Base):
    """An account (PRD S1). The first one is the admin."""

    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str] = mapped_column(Text)
    # Empty when this account signs in with Google only (S16). auth/passwords.py answers an
    # empty hash exactly as it answers a wrong password, so there is nothing to sign in to.
    password_hash: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default="false")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_sign_ins: Mapped[int] = mapped_column(Integer, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    totp_secret: Mapped[str | None] = mapped_column(Text)
    totp_pending_secret: Mapped[str | None] = mapped_column(Text)
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    recovery_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    # IDEA-16: this account's own forwarding address for the codes a job site emails,
    # jap+<code_token>@... None until they ask for one.
    code_token: Mapped[str | None] = mapped_column(Text)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_sign_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The terms and privacy notice this account agreed to at sign-up (PRD S14). Null on an
    # account made before consent existed: it is asked once on its next visit.
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_version: Mapped[str | None] = mapped_column(Text)
    # What the admin set for this account in place of the plan's numbers (None: the plan's).
    limit_ai_usd: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    limit_boards: Mapped[int | None] = mapped_column(Integer)
    limit_applications: Mapped[int | None] = mapped_column(Integer)


class UserIdentity(Timestamps, Base):
    """An account somewhere else that signs in to this one (PRD S16). Found by
    (provider, subject): the provider's own id for the person, never their email."""

    __tablename__ = "user_identity"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    provider: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)  # what they said then; never looked up by


class AuthSession(Timestamps, Base):
    """A signed-in browser (stage signed_in) or a sign-in waiting for its code (two_step)."""

    __tablename__ = "auth_session"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_user.id"))
    token_hash: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text)
    code_tries: Mapped[int] = mapped_column(Integer, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    user_agent: Mapped[str | None] = mapped_column(Text)


class PasswordReset(Timestamps, Base):
    """An emailed reset link (PRD S4): single use, 30 minutes."""

    __tablename__ = "password_reset"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_user.id"))
    token_hash: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailVerification(Timestamps, Base):
    """An emailed link that proves a new account's address (PRD S10): single use, 24 hours."""

    __tablename__ = "email_verification"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_user.id"))
    token_hash: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RateLimit(Base):
    """Tries per key (an address or an email, per route) in one time window (auth/limits.py)."""

    __tablename__ = "rate_limit"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, server_default="0")


class AppSetting(Base):
    """A switch the admin sets for the whole service (PRD S12), such as sign_up_paused."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class LogEvent(Base):
    """A warning or error, or a step line kept for an application timeline (PRD O3, O8)."""

    __tablename__ = "log_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    level: Mapped[str] = mapped_column(Text)
    event: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    logger: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    application_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    inngest_run_id: Mapped[str | None] = mapped_column(Text)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")


class RouteStat(Base):
    """API requests per route per hour (PRD O4, O5)."""

    __tablename__ = "route_stat"

    hour: Mapped[datetime] = mapped_column(primary_key=True)
    method: Mapped[str] = mapped_column(Text, primary_key=True)
    route: Mapped[str] = mapped_column(Text, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, server_default="0")
    errors: Mapped[int] = mapped_column(Integer, server_default="0")
    total_ms: Mapped[int] = mapped_column(BigInteger, server_default="0")
    max_ms: Mapped[int] = mapped_column(Integer, server_default="0")


class AlertState(Base):
    """When a health alert was last emailed (PRD O6): at most once a day per key."""

    __tablename__ = "alert_state"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    last_sent_at: Mapped[datetime]
    detail: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # None: the service
