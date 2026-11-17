"""Filtered listing (F1 to F6): which listings match My profile, as one SQL statement.

A listing is listed when it satisfies at least one combination of the match rule (every
condition in that combination passes) and the always-on filters: its work mode is one I
ticked (or unknown, shown as Work mode unclear) and neither its company nor a title word is
excluded. The result is written to my user_listing row for it: matched (the conditions it
met; null = not listed) and matched_role (the first target role it matches, for Jobs by
role). Nothing is fetched, so a profile change updates the list in well under a second.

Listings are shared and matching is per user (PRD S7): one user's filter reads only the
listings of boards they follow and writes only their own rows, so two people following one
company get two lists from one fetch. A row is kept while the listing matches or has a
status, and removed when it does neither.

Skills never hide a job (PRD 1.21, F5). The same statement writes each listing's skills
match instead: required_hit and nice_hit (my required and nice-to-have skills the job asks
for) and required_pct (the share of my required skills, null when the job states no skills
or I have none required: unknown, not 0). Jobs sorts and filters by it, and Automatic mode
applies only at or above a bar (F6, pipeline/apply/flow.py).

Conditions:
- Role: the canonical role contains one of my target roles as whole words, after the same
  cleaning as dedup (Sr to Senior, locations dropped); engineer and developer count alike.
- Location: a job location names one of my locations, or the job is remote and allows one
  of my Remote regions, or it is Remote - Worldwide (always counts), or I chose All remote
  jobs (remote_any).
"""

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database import tenant
from database.models import BoardFollow, Posting, Profile, UserListing
from pipeline.canonical import canonical_role, clean_company
from pipeline.workspace import FOLLOWED_SQL, OPEN_SQL, profile_of

CONDITIONS = ("role", "location")
DEFAULT_RULE = (("role", "location"),)  # job title and location, as the database default
ROLE_SYNONYMS = {"engineer": ("engineer", "developer"), "developer": ("engineer", "developer")}
PLACE_SYNONYMS = [
    {"bangalore", "bengaluru"}, {"bombay", "mumbai"}, {"gurgaon", "gurugram"},
    {"madras", "chennai"}, {"calcutta", "kolkata"}, {"new delhi", "delhi"},
    {"us", "usa", "united states", "united states of america"},
    {"uk", "united kingdom", "great britain"}, {"nyc", "new york"},
    {"sf", "san francisco"}, {"uae", "united arab emirates"},
]  # fmt: skip
# Words that say "remote" rather than name a place; my Remote regions decide those jobs.
REMOTE_WORDS = {"remote", "anywhere", "worldwide", "global", "wfh", "work from home"}
WORLDWIDE = r"(^|[^a-z0-9])(worldwide|anywhere|global)([^a-z0-9]|$)"


class ProfileLike(Protocol):
    target_roles: list[str]
    must_have_skills: list[str]
    nice_to_have_skills: list[str]
    locations: list[str]
    remote_regions: list[str]
    remote_any: bool
    match_rule: Any
    work_modes: Any
    excluded_companies: list[str]
    excluded_title_words: list[str]


@dataclass(frozen=True)
class Criteria:
    target_roles: tuple[str, ...] = ()
    must_have_skills: tuple[str, ...] = ()
    nice_to_have_skills: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    remote_regions: tuple[str, ...] = ()
    remote_any: bool = False
    match_rule: tuple[tuple[str, ...], ...] = DEFAULT_RULE
    work_modes: tuple[str, ...] = ("remote", "hybrid", "onsite")
    excluded_companies: tuple[str, ...] = ()
    excluded_title_words: tuple[str, ...] = ()

    @classmethod
    def of(cls, p: ProfileLike) -> "Criteria":
        return cls(
            match_rule=clean_rule(p.match_rule),
            target_roles=tuple(p.target_roles or ()),
            must_have_skills=tuple(s.lower() for s in p.must_have_skills or ()),
            nice_to_have_skills=tuple(s.lower() for s in p.nice_to_have_skills or ()),
            locations=tuple(p.locations or ()),
            remote_regions=tuple(p.remote_regions or ()),
            remote_any=bool(p.remote_any),
            work_modes=tuple(str(m) for m in p.work_modes or ()),
            excluded_companies=tuple(p.excluded_companies or ()),
            excluded_title_words=tuple(p.excluded_title_words or ()),
        )


def clean_rule(rule: Any) -> tuple[tuple[str, ...], ...]:
    """A saved match rule as it is used: role and location only. A rule saved before 1.21
    that used skills keeps its other conditions; a combination of skills alone is dropped,
    and a rule left empty becomes job title and location."""
    out: list[tuple[str, ...]] = []
    for combo in rule or ():
        kept = tuple(c for c in CONDITIONS if c in combo)
        if kept and kept not in out:
            out.append(kept)
    return tuple(out) or DEFAULT_RULE


def _esc(word: str) -> str:
    """Escape a literal for a Postgres (and Python) regular expression."""
    return re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", word)


def _whole(alternatives: Iterable[str]) -> str:
    alts = sorted({_esc(a) for a in alternatives if a}, key=len, reverse=True)
    return rf"(^|[^a-z0-9])({'|'.join(alts)})([^a-z0-9]|$)"


def role_pattern(role: str) -> str | None:
    """Senior Backend Engineer -> a pattern matching 'senior backend (engineer|developer)'."""
    words = (canonical_role(role) or role.lower()).split()
    if not words:
        return None
    parts = []
    for w in words:
        alts = ROLE_SYNONYMS.get(w, (w,))
        parts.append(_esc(alts[0]) if len(alts) == 1 else f"({'|'.join(map(_esc, alts))})")
    return rf"(^|[^a-z0-9])({' '.join(parts)})([^a-z0-9]|$)"


def place_pattern(place: str) -> str | None:
    """Bangalore -> matches Bangalore or Bengaluru as whole words. Remote words give None.

    A place typed with its region ("Bangalore, India", "Pune - Maharashtra") goes by its
    first part, the most specific one, so it still matches "Bengaluru, Karnataka, India".
    """
    parts = [" ".join(p.split()) for p in re.split(r"[,/|;()·]+|\s+-\s+", place.lower())]
    parts = [p for p in parts if p and p not in REMOTE_WORDS]
    if not parts:
        return None
    name = parts[0]
    names = {name}
    for group in PLACE_SYNONYMS:
        if name in group:
            names |= group
    return _whole(names)


def title_word_pattern(word: str) -> str | None:
    word = " ".join(word.lower().split())
    return _whole([word]) if word else None


def _rule_sql(rule: Sequence[Sequence[str]]) -> str:
    alias = {"role": "f.r", "location": "f.g"}
    return " OR ".join("(" + " AND ".join(alias[c] for c in combo) + ")" for combo in rule)


def matches_sql(c: Criteria, user_id: uuid.UUID | None = None) -> tuple[str, dict[str, Any]]:
    """SELECT id, matched, matched_role and the skills match for every listing (or those in
    :ids when given). With a user, only the listings that are theirs to see: on a board
    they follow, or already carrying a row of theirs."""
    roles = [(r, p) for r in c.target_roles if (p := role_pattern(r))]
    params: dict[str, Any] = {
        "role_patterns": [p for _, p in roles],
        "must": list(c.must_have_skills),
        "nice": [s for s in c.nice_to_have_skills if s not in c.must_have_skills],
        "loc_patterns": [p for loc in c.locations if (p := place_pattern(loc))],
        "region_patterns": [p for r in c.remote_regions if (p := place_pattern(r))],
        "worldwide": WORLDWIDE,
        "remote_any": c.remote_any,
        "modes": list(c.work_modes),
        "ex_names": [n.lower().strip() for n in c.excluded_companies],
        "ex_canon": [clean_company(n) for n in c.excluded_companies],
        "title_patterns": [p for w in c.excluded_title_words if (p := title_word_pattern(w))],
    }
    whens = []
    for i, (role, pattern) in enumerate(roles):
        params[f"rp{i}"], params[f"rn{i}"] = pattern, role
        whens.append(f"WHEN l.canonical_role ~ :rp{i} THEN CAST(:rn{i} AS text)")
    role_case = f"CASE {' '.join(whens)} END" if whens else "CAST(NULL AS text)"
    scope = ""
    if user_id is not None:
        params["uid"] = user_id
        scope = f"""AND ({FOLLOWED_SQL} OR EXISTS (
          SELECT 1 FROM user_listing ul WHERE ul.listing_id = l.id AND ul.user_id = :uid))"""
    sql = f"""
    WITH f AS (
      SELECT l.id,
        COALESCE(l.canonical_role ~ ANY(CAST(:role_patterns AS text[])), false) AS r,
        ARRAY(SELECT s FROM unnest(CAST(:must AS text[])) WITH ORDINALITY u(s, o)
              WHERE s = ANY(l.skills) ORDER BY o) AS req,
        ARRAY(SELECT s FROM unnest(CAST(:nice AS text[])) WITH ORDINALITY u(s, o)
              WHERE s = ANY(l.skills) ORDER BY o) AS nice,
        (cardinality(l.skills) > 0 AND cardinality(CAST(:must AS text[])) > 0) AS known,
        (
          EXISTS (SELECT 1 FROM unnest(l.locations) loc
                  WHERE lower(loc) ~ ANY(CAST(:loc_patterns AS text[])))
          OR (
            (l.work_mode = 'remote'
             OR EXISTS (SELECT 1 FROM unnest(l.locations) loc WHERE lower(loc) ~ 'remote'))
            AND (
              CAST(:remote_any AS boolean)
              OR EXISTS (SELECT 1 FROM unnest(l.remote_regions) rr WHERE lower(rr) ~ :worldwide)
              OR EXISTS (SELECT 1 FROM unnest(l.locations) loc WHERE lower(loc) ~ :worldwide)
              OR EXISTS (SELECT 1 FROM unnest(l.remote_regions) rr
                         WHERE lower(rr) ~ ANY(CAST(:region_patterns AS text[])))
              OR EXISTS (SELECT 1 FROM unnest(l.locations) loc
                         WHERE lower(loc) ~ 'remote'
                           AND lower(loc) ~ ANY(CAST(:region_patterns AS text[])))
            )
          )
        ) AS g,
        (
          (l.work_mode IS NULL OR CAST(l.work_mode AS text) = ANY(CAST(:modes AS text[])))
          AND NOT (lower(c.name) = ANY(CAST(:ex_names AS text[]))
                   OR c.canonical_name = ANY(CAST(:ex_canon AS text[])))
          AND NOT COALESCE(lower(l.title_display) ~ ANY(CAST(:title_patterns AS text[])), false)
        ) AS keep,
        {role_case} AS mrole
      FROM listing l JOIN company c ON c.id = l.company_id
      WHERE (CAST(:ids AS uuid[]) IS NULL OR l.id = ANY(CAST(:ids AS uuid[])))
        {scope}
    )
    SELECT f.id,
      CASE WHEN f.keep AND ({_rule_sql(c.match_rule)}) THEN array_remove(ARRAY[
        CASE WHEN f.r THEN 'role' END, CASE WHEN f.g THEN 'location' END], NULL) END AS matched,
      CASE WHEN f.keep AND ({_rule_sql(c.match_rule)}) THEN f.mrole END AS matched_role,
      f.keep AND f.r AS role_ok, f.keep AND f.g AS location_ok,
      f.req AS required_hit, f.nice AS nice_hit,
      CASE WHEN f.known THEN CAST(round(100.0 * cardinality(f.req)
        / cardinality(CAST(:must AS text[]))) AS smallint) END AS required_pct
    FROM f
    """
    return sql, params


def refilter_sql(
    criteria: Criteria, user_id: uuid.UUID, ids: Iterable[uuid.UUID] | None = None
) -> tuple[str, dict[str, Any]]:
    """The statement that writes what matches_sql works out to one user's rows, touching
    only those that change: a listing that matches gets its row (new ones start Not applied,
    or Closed when no board they follow has it open), one that stopped matching keeps its
    row only while it has a status worth keeping."""
    sql, params = matches_sql(criteria, user_id)
    params["ids"] = None if ids is None else list(ids)
    statement = f"""
        WITH m AS MATERIALIZED ({sql}),
        listed AS (
          INSERT INTO user_listing AS ul (user_id, listing_id, status, matched, matched_role,
                                          required_hit, nice_hit, required_pct)
          SELECT :uid, l.id,
                 CAST(CASE WHEN {OPEN_SQL} THEN 'not_applied' ELSE 'closed' END AS listing_status),
                 m.matched, m.matched_role, m.required_hit, m.nice_hit, m.required_pct
          FROM m JOIN listing l ON l.id = m.id
          WHERE m.matched IS NOT NULL
          ON CONFLICT (user_id, listing_id) DO UPDATE SET
            matched = EXCLUDED.matched, matched_role = EXCLUDED.matched_role,
            required_hit = EXCLUDED.required_hit, nice_hit = EXCLUDED.nice_hit,
            required_pct = EXCLUDED.required_pct, updated_at = now()
          WHERE ul.matched IS DISTINCT FROM EXCLUDED.matched
             OR ul.matched_role IS DISTINCT FROM EXCLUDED.matched_role
             OR ul.required_hit IS DISTINCT FROM EXCLUDED.required_hit
             OR ul.nice_hit IS DISTINCT FROM EXCLUDED.nice_hit
             OR ul.required_pct IS DISTINCT FROM EXCLUDED.required_pct
        ),
        unlisted AS (
          UPDATE user_listing ul SET matched = NULL, matched_role = NULL,
            required_hit = m.required_hit, nice_hit = m.nice_hit,
            required_pct = m.required_pct, updated_at = now()
          FROM m
          WHERE ul.user_id = :uid AND ul.listing_id = m.id AND m.matched IS NULL
            AND ul.status NOT IN ('not_applied', 'closed')
            AND (ul.matched IS NOT NULL
                 OR ul.required_hit IS DISTINCT FROM m.required_hit
                 OR ul.nice_hit IS DISTINCT FROM m.nice_hit
                 OR ul.required_pct IS DISTINCT FROM m.required_pct)
        )
        DELETE FROM user_listing ul USING m
        WHERE ul.user_id = :uid AND ul.listing_id = m.id AND m.matched IS NULL
          AND ul.status IN ('not_applied', 'closed')
    """
    return statement, params


# Listed and shown by default: not Closed for me, and on a board I still follow.
VISIBLE = f"""
  ul.status <> 'closed' AND {FOLLOWED_SQL}
"""


async def refilter(
    session: AsyncSession,
    criteria: Criteria,
    ids: Iterable[uuid.UUID] | None = None,
    user_id: uuid.UUID | None = None,
) -> int:
    """Write one user's matched, matched_role and skills match (all their listings, or only
    ids): the session's user unless another is named. Returns how many are listed and
    visible now. The caller commits."""
    uid = user_id or tenant.user_id(session)
    sql, params = refilter_sql(criteria, uid, ids)
    await session.execute(text(sql), params)
    return await listed_count(session, uid)


async def listed_count(session: AsyncSession, user_id: uuid.UUID) -> int:
    count = await session.scalar(
        text(
            "SELECT count(*) FROM user_listing ul JOIN listing l ON l.id = ul.listing_id "
            f"WHERE ul.user_id = :uid AND ul.matched IS NOT NULL AND {VISIBLE}"
        ),
        {"uid": user_id},
    )
    return int(count or 0)


async def preview_counts(session: AsyncSession, criteria: Criteria) -> dict[str, int]:
    """How many listings would be listed with these settings, and how many pass each
    condition on its own (after work mode and exclusions), without saving anything."""
    sql, params = matches_sql(criteria, tenant.user_id(session))
    params["ids"] = None
    row = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE m.matched IS NOT NULL), "
                "count(*) FILTER (WHERE m.role_ok), "
                f"count(*) FILTER (WHERE m.location_ok) FROM ({sql}) m "
                "JOIN listing l ON l.id = m.id "
                "LEFT JOIN user_listing ul ON ul.listing_id = l.id AND ul.user_id = :uid "
                f"WHERE {FOLLOWED_SQL} AND COALESCE(CAST(ul.status AS text), "
                f"CASE WHEN {OPEN_SQL} THEN 'not_applied' ELSE 'closed' END) <> 'closed'"
            ),
            params,
        )
    ).one()
    return dict(zip(("matching", "role", "location"), map(int, row), strict=True))


async def refilter_from_profile(
    session: AsyncSession, ids: Iterable[uuid.UUID] | None = None
) -> int:
    """refilter with the session user's saved profile."""
    profile = await profile_of(session)
    return await refilter(session, Criteria.of(profile) if profile else Criteria(), ids)


async def refilter_users(
    session: AsyncSession, ids: Iterable[uuid.UUID] | None = None, *, board_ids: Any = None
) -> int:
    """refilter for everyone shared data just changed for (a fetch, a posting read by the
    AI, a merge). With listings: each user who follows one of their boards or has a row on
    one of them, and only those listings. With boards (ids or a SELECT of them): each user
    who follows one. With neither: every user with a profile. Run as the service; as one
    user it reaches only that user. Returns how many users were filtered."""
    wanted = None if ids is None else list(ids)
    users: Any
    if wanted is None and board_ids is not None:
        users = (
            select(BoardFollow.user_id)
            .where(BoardFollow.active, BoardFollow.board_id.in_(board_ids))
            .distinct()
        )
    elif wanted is None:
        users = select(Profile.user_id)
    else:
        users = (
            select(BoardFollow.user_id)
            .join(Posting, Posting.watched_board_id == BoardFollow.board_id)
            .where(BoardFollow.active, Posting.listing_id.in_(wanted))
            .union(select(UserListing.user_id).where(UserListing.listing_id.in_(wanted)))
        )
    found = list(dict.fromkeys(await session.scalars(users)))
    for uid in found:
        profile = await profile_of(session, uid)
        criteria = Criteria.of(profile) if profile else Criteria()
        sql, params = refilter_sql(criteria, uid, wanted)
        await session.execute(text(sql), params)
    return len(found)
