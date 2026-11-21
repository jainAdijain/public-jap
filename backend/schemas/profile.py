"""My profile: what the UI reads and saves (U7 to U10)."""

from datetime import datetime, time
from typing import Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator

WorkMode = Literal["remote", "hybrid", "onsite"]
Condition = Literal["role", "location"]  # skills never hide a job (PRD 1.21, F5)
ApplyMode = Literal["review", "automatic"]


class Link(BaseModel):
    label: str = Field(max_length=40)
    url: HttpUrl


def _clean(items: list[str]) -> list[str]:
    """Trim, drop blanks and case-insensitive duplicates, keep order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = " ".join(item.split())
        if value and value.lower() not in seen:
            seen.add(value.lower())
            out.append(value)
    return out


class ProfileIn(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=500)
    links: list[Link] = []
    target_roles: list[str] = []
    must_have_skills: list[str] = []
    nice_to_have_skills: list[str] = []
    locations: list[str] = []
    remote_regions: list[str] = []
    remote_any: bool = False  # every remote job passes Location, whatever its regions
    match_rule: list[list[Condition]] = [["location", "role"]]  # job title and location
    work_modes: list[WorkMode] = ["remote", "hybrid", "onsite"]
    excluded_companies: list[str] = []
    excluded_title_words: list[str] = []
    tone_note: str | None = Field(default=None, max_length=500)

    @field_validator(
        "target_roles",
        "locations",
        "remote_regions",
        "excluded_companies",
        "excluded_title_words",
    )
    @classmethod
    def clean_list(cls, v: list[str]) -> list[str]:
        return _clean(v)

    @field_validator("must_have_skills", "nice_to_have_skills")
    @classmethod
    def clean_skills(cls, v: list[str]) -> list[str]:
        return _clean([s.lower() for s in v])

    @field_validator("work_modes")
    @classmethod
    def at_least_one_mode(cls, v: list[WorkMode]) -> list[WorkMode]:
        if not v:
            raise ValueError("Tick at least one work mode")
        return list(dict.fromkeys(v))

    @field_validator("match_rule", mode="before")
    @classmethod
    def without_skills(cls, v: object) -> object:
        """A rule from before 1.21 may still name skills: it keeps its other conditions, and
        one left with nothing becomes job title and location."""
        if not isinstance(v, list) or not any(isinstance(c, list) and "skills" in c for c in v):
            return v
        kept = [[x for x in c if x != "skills"] for c in v if isinstance(c, list)]
        return [c for c in kept if c] or [["location", "role"]]

    @field_validator("match_rule")
    @classmethod
    def valid_rule(cls, v: list[list[Condition]]) -> list[list[Condition]]:
        combos = [sorted(set(c)) for c in v if c]
        if not combos:
            raise ValueError("The match rule needs at least one combination")
        unique: list[list[Condition]] = []
        for c in combos:
            if c not in unique:
                unique.append(c)
        return unique

    @field_validator("full_name", "phone", "address", "tone_note")
    @classmethod
    def blank_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None


class Spend(BaseModel):
    month_usd: float
    cap_usd: float
    reached: bool


class ApiKey(BaseModel):
    """A user's own OpenRouter key as Settings shows it (IDEA-19). Never the key itself."""

    # False when JAP_SECRET_KEY is unset: no key can be saved on this stack.
    available: bool
    set: bool
    hint: str = ""  # the last four characters of the saved key
    saved_at: datetime | None = None


class SaveApiKey(BaseModel):
    api_key: str = Field(min_length=16, max_length=400)


class CodeAddress(BaseModel):
    """Where this account's mail filter should forward security-code emails (IDEA-16)."""

    address: str | None  # None until they ask for one
    mailbox: bool  # whether this JAP has a mailbox to forward to at all


class Plan(BaseModel):
    """What this account may use (PRD S12), as Settings shows it."""

    name: str  # Free, or Admin
    boards: int | None  # companies followed; None: no limit
    applications_per_day: int
    ai_usd: float  # AI spend a month
    custom: bool = False  # the admin set at least one of these for this account


class ProfileOut(ProfileIn):
    email: str | None = None  # stored values are returned as saved
    apply_mode: ApplyMode
    daily_run: bool
    daily_run_at: time
    daily_cap: int
    auto_apply_min_skills: int  # F6: Automatic applies at or above this % of required skills
    spend: Spend
    plan: Plan
    # Consent (S14): JAP may apply in my name, and may do it without asking.
    submit_opt_in: bool = False
    auto_opt_in: bool = False
    # IDEA-17: sending paused for this account; applications wait rather than fail.
    submit_paused: bool = False
    # The zone the daily search goes by (U22). None: the service's JAP_TZ, which is what
    # every account used before they could say.
    tz: str | None = None


class ProfileSaved(ProfileOut):
    matching_count: int


class MatchPreview(BaseModel):
    matching_count: int
    # Listings that pass each condition on its own, so a rule that lists nothing shows why.
    by_condition: dict[Condition, int]


class ProfileSwitches(BaseModel):
    apply_mode: ApplyMode | None = None
    daily_run: bool | None = None
    daily_run_at: time | None = None  # in this account's tz; 06:00 unless changed
    # The zone daily_run_at is read in (U22). It lives here and not on ProfileIn because
    # Job preferences PUTs the whole of ProfileIn, and a page that does not know about the
    # zone would send it back as null and wipe it.
    tz: str | None = Field(default=None, max_length=64)
    daily_cap: int | None = Field(default=None, ge=0, le=100)  # A1: 20 unless lowered
    spend_cap_usd: float | None = Field(default=None, ge=0, le=100)  # $2 unless changed
    auto_apply_min_skills: int | None = Field(default=None, ge=0, le=100)  # F6: 60, 0 = off
    # Consent (S14), each given or taken back on its own. Taking back the first takes back
    # the second with it, and turns Automatic mode off.
    submit_opt_in: bool | None = None
    auto_opt_in: bool | None = None
    # IDEA-17: pause or resume this account's sending. Not consent and not the account
    # being off: prepared applications wait, and carry on when it goes back.
    submit_paused: bool | None = None

    @field_validator("tz")
    @classmethod
    def known_zone(cls, v: str | None) -> str | None:
        """A name from the IANA database, the same list the browser offers. Anything else
        would make ZoneInfo raise in the daily trigger, where nobody is there to see it."""
        name = (v or "").strip()
        if not name:
            return None
        if name not in available_timezones():
            raise ValueError(f"{name} is not a time zone name")
        return name
