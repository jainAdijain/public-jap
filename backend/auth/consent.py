"""What a user has agreed to, and the version of what they agreed to (PRD S14).

JAP holds a person's name, address, phone, resume and the answers they give to employers,
sends job and resume text to a model with the contact details taken out, and presses Submit
on a form in their name. Each of those is told to them and agreed to before it happens:

- the terms and the privacy notice, ticked at sign-up and kept with the version that was
  shown, so a later notice can be asked for again rather than assumed;
- an opt-in before JAP submits anything in their name;
- a second opt-in before it does that without asking first (Automatic mode).

TERMS_VERSION is the date the notice last changed. Raising it asks everyone again, so it
changes only when what the notice says changes, not when its wording is tidied.
"""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AppUser, Profile

TERMS_VERSION = "2026-11-22"

# What the API says when an agreement is missing. Each is a sentence for the user, and the
# screen that shows it offers the tick that fixes it.
NEEDS_TERMS = "Agree to the terms and the privacy notice first"
NEEDS_SUBMIT = "Agree to JAP applying in your name first"
NEEDS_AUTO = "Agree to JAP applying without asking first"


def accepted_terms(user: AppUser) -> bool:
    """Whether this account agreed to the notice as it stands now."""
    return user.terms_accepted_at is not None and user.terms_version == TERMS_VERSION


def accept_terms(user: AppUser, when: datetime) -> None:
    user.terms_accepted_at = when
    user.terms_version = TERMS_VERSION


def may_submit(profile: Profile | None) -> bool:
    return profile is not None and profile.submit_opt_in_at is not None


def may_apply_automatically(profile: Profile | None) -> bool:
    """Both opt-ins: the wider one to apply at all, and this one to do it without asking.
    The repeated None is for the type checker, which does not read it out of may_submit."""
    return may_submit(profile) and profile is not None and profile.auto_opt_in_at is not None


async def may_submit_for(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """Read the opt-in fresh: it can be withdrawn while an attempt waits for review, and
    the attempt must not be submitted after that (checked again in apply/flow.submit)."""
    from pipeline.workspace import profile_of

    return may_submit(await profile_of(session, user_id))
