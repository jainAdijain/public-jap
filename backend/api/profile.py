"""GET, PUT and PATCH /api/profile (U7 to U10): the signed-in user's own profile (S7)."""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ApiError
from auth import consent
from config import get_settings
from database import tenant
from database.models import AppUser, Profile, UserSecret
from database.session import Session
from functions.events import send
from llm import secrets
from llm.spend import month_spend
from pipeline.apply import security_code
from pipeline.limits import Limits, limits_for
from pipeline.matching import Criteria, preview_counts, refilter
from pipeline.resume import normalize_skills, skill_aliases
from pipeline.workspace import load_profile
from schemas.profile import (
    ApiKey,
    CodeAddress,
    MatchPreview,
    Plan,
    ProfileIn,
    ProfileOut,
    ProfileSaved,
    ProfileSwitches,
    SaveApiKey,
)

router = APIRouter(prefix="/profile", tags=["profile"])

FIELDS = list(ProfileIn.model_fields)


def _consent(profile: Profile, body: ProfileSwitches) -> None:
    """Record the two opt-ins (S14). Taking back "JAP may apply for me" takes the Automatic
    one with it and puts the account back in Review mode: the narrower permission cannot
    outlive the wider one."""
    now = datetime.now(UTC)
    if body.submit_opt_in is not None:
        profile.submit_opt_in_at = now if body.submit_opt_in else None
    if body.auto_opt_in is not None:
        profile.auto_opt_in_at = now if body.auto_opt_in else None
    if profile.submit_opt_in_at is None:
        profile.auto_opt_in_at = None
    if profile.auto_opt_in_at is None and profile.apply_mode == "automatic":
        profile.apply_mode = "review"


def plan_out(limits: Limits) -> Plan:
    return Plan(
        name=limits.plan,
        boards=limits.boards,
        applications_per_day=limits.plan_applications,
        ai_usd=limits.plan_ai_usd,
        custom=limits.custom,
    )


async def to_out(session: AsyncSession, profile: Profile) -> ProfileOut:
    data = {f: getattr(profile, f) for f in FIELDS}
    limits = await limits_for(session, profile.user_id)
    data.update(
        apply_mode=profile.apply_mode,
        daily_run=profile.daily_run,
        daily_run_at=profile.daily_run_at,
        daily_cap=limits.applications_per_day,  # what is really sent: never over the plan
        auto_apply_min_skills=profile.auto_apply_min_skills,
        spend=await month_spend(session),
        plan=plan_out(limits),
        submit_opt_in=profile.submit_opt_in_at is not None,
        auto_opt_in=profile.auto_opt_in_at is not None,
        submit_paused=profile.submit_paused,  # IDEA-17
        tz=profile.tz,  # U22; None means the service's JAP_TZ
    )
    return ProfileOut.model_validate(data)


@router.get("", response_model=ProfileOut)
async def get_profile(session: Session) -> ProfileOut:
    return await to_out(session, await load_profile(session))


# IDEA-19: the account's own OpenRouter key. The key is write-only — it goes in, and only
# its last four characters ever come back out.


async def _key_out(session: AsyncSession) -> ApiKey:
    available = secrets.configured()
    row = await session.get(UserSecret, (tenant.user_id(session), secrets.OPENROUTER))
    return ApiKey(
        available=available,
        set=row is not None,
        hint=row.hint if row else "",
        saved_at=row.updated_at if row else None,
    )


@router.get("/api-key", response_model=ApiKey)
async def get_api_key(session: Session) -> ApiKey:
    return await _key_out(session)


@router.put("/api-key", response_model=ApiKey)
async def save_api_key(body: SaveApiKey, session: Session) -> ApiKey:
    """Save this account's own OpenRouter key: from now on their work is billed to it and
    their JAP allowance is left alone. Stored sealed (llm/secrets.py), never logged."""
    if not secrets.configured():
        raise ApiError(503, "secrets_unavailable", "This JAP cannot store keys yet. Ask the admin.")
    key = body.api_key.strip()
    if len(key) < secrets.MIN_KEY_LENGTH:
        raise ApiError(422, "bad_key", "That does not look like an API key")
    uid = tenant.user_id(session)
    ciphertext, nonce = secrets.seal(key, uid, secrets.OPENROUTER)
    row = await session.get(UserSecret, (uid, secrets.OPENROUTER))
    if row is None:
        row = UserSecret(user_id=uid, kind=secrets.OPENROUTER)
        session.add(row)
    row.ciphertext, row.nonce, row.hint = ciphertext, nonce, secrets.hint_of(key)
    await session.commit()
    return await _key_out(session)


# IDEA-16: the address this account's mail filter should forward security-code emails to.


async def _code_address(session: AsyncSession, make: bool) -> CodeAddress:
    user = await session.get(AppUser, tenant.user_id(session))
    if user is None:
        raise ApiError(404, "not_found", "No such account")
    if make and not user.code_token:
        user.code_token = security_code.new_token()
        await session.commit()
    return CodeAddress(
        address=security_code.address_for(user.code_token) if user.code_token else None,
        mailbox=bool(get_settings().email_from),
    )


@router.get("/code-address", response_model=CodeAddress)
async def get_code_address(session: Session) -> CodeAddress:
    return await _code_address(session, make=False)


@router.post("/code-address", response_model=CodeAddress)
async def make_code_address(session: Session) -> CodeAddress:
    """Give this account its own forwarding address, so a code emailed to it is matched
    to them and never to somebody else applying at the same moment."""
    return await _code_address(session, make=True)


@router.delete("/api-key", response_model=ApiKey)
async def clear_api_key(session: Session) -> ApiKey:
    """Take the key off. The account goes back to JAP's key and its monthly allowance."""
    row = await session.get(UserSecret, (tenant.user_id(session), secrets.OPENROUTER))
    if row is not None:
        await session.delete(row)
        await session.commit()
    return await _key_out(session)


async def normalized(session: AsyncSession, body: ProfileIn) -> dict[str, object]:
    """The profile as it is stored: skills through skill_alias, nice-to-have minus must-have."""
    aliases = await skill_aliases(session)
    data = body.model_dump(mode="json")
    data["must_have_skills"] = normalize_skills(body.must_have_skills, aliases)
    data["nice_to_have_skills"] = [
        s
        for s in normalize_skills(body.nice_to_have_skills, aliases)
        if s not in data["must_have_skills"]
    ]
    return data


@router.put("", response_model=ProfileSaved)
async def save_profile(body: ProfileIn, session: Session) -> ProfileSaved:
    """Save, then re-filter the stored listings at once (T2, F1). When a job preference
    changed, profile/saved then starts a fetch in the background (profile-saved, debounced),
    so new matching jobs follow."""
    profile = await load_profile(session)
    before = Criteria.of(profile)
    for field, value in (await normalized(session, body)).items():
        setattr(profile, field, value)
    await session.flush()
    matching = await refilter(session, Criteria.of(profile))
    await session.commit()
    await session.refresh(profile)
    if Criteria.of(profile) != before:  # contact details or the letter style alone: no search
        await send("profile/saved", {"user_id": str(tenant.user_id(session))})
    out = await to_out(session, profile)
    return ProfileSaved(**out.model_dump(), matching_count=matching)


@router.post("/preview", response_model=MatchPreview)
async def preview(body: ProfileIn, session: Session) -> MatchPreview:
    """How many listings these settings would list, before saving (U10). Saves nothing."""
    data = await normalized(session, body)
    criteria = Criteria.of(ProfileIn.model_validate(data))
    counts = await preview_counts(session, criteria)
    return MatchPreview(
        matching_count=counts.pop("matching"),
        by_condition={"role": counts["role"], "location": counts["location"]},
    )


@router.patch("", response_model=ProfileOut)
async def flip_switches(body: ProfileSwitches, session: Session) -> ProfileOut:
    profile = await load_profile(session)
    _consent(profile, body)
    if body.apply_mode is not None:
        if body.apply_mode == "automatic" and not consent.may_apply_automatically(profile):
            raise ApiError(409, "consent_needed", consent.NEEDS_AUTO)
        profile.apply_mode = body.apply_mode
    if body.daily_run is not None:
        profile.daily_run = body.daily_run
    if body.daily_run_at is not None:
        profile.daily_run_at = body.daily_run_at.replace(second=0, microsecond=0, tzinfo=None)
    if "tz" in body.model_fields_set:  # null is a real value here: back to the service's zone
        profile.tz = body.tz
    if body.daily_cap is not None:
        profile.daily_cap = body.daily_cap
    if body.auto_apply_min_skills is not None:
        profile.auto_apply_min_skills = body.auto_apply_min_skills
    if body.spend_cap_usd is not None:
        profile.spend_cap_usd = Decimal(str(round(body.spend_cap_usd, 2)))
    if body.submit_paused is not None:
        profile.submit_paused = body.submit_paused
    await session.commit()
    await session.refresh(profile)
    return await to_out(session, profile)
