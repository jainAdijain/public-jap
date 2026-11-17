"""The owner's side of the shared listings, for tests that set up or read it by hand.

Listings are shared and what one is to a user lives in user_listing (PRD S7). These work on
the `sql` connection, whose app.user_id is the owner account.
"""

from typing import Any

import psycopg

ME = "current_setting('app.user_id')::uuid"

# Listings as the owner sees them: every listing, with their matched, skills match and
# status. One with no row of theirs is Not applied while a board they follow has it open,
# else Closed, which is what the API answers too (pipeline/workspace.py).
LISTINGS = f"""(
  SELECT l.*, u.matched, u.matched_role, u.required_hit, u.nice_hit, u.required_pct,
    u.applied_at,
    COALESCE(u.status::text, CASE WHEN EXISTS (
      SELECT 1 FROM posting p JOIN board_follow f ON f.board_id = p.watched_board_id
      WHERE p.listing_id = l.id AND p.state = 'open' AND f.user_id = {ME} AND f.active
    ) THEN 'not_applied' ELSE 'closed' END) AS status
  FROM listing l LEFT JOIN user_listing u ON u.listing_id = l.id AND u.user_id = {ME}
)"""


def follow(sql: psycopg.Connection[Any], where: str = "true") -> None:
    """The owner follows these boards (all of them)."""
    sql.execute(
        f"INSERT INTO board_follow (board_id) SELECT id FROM watched_board WHERE {where}"
        " ON CONFLICT (user_id, board_id) DO UPDATE SET active = true"
    )


def listed(sql: psycopg.Connection[Any], where: str = "true", matched: str = "{role}") -> None:
    """These listings (all of them) match the owner's profile."""
    sql.execute(
        f"INSERT INTO user_listing (listing_id, matched) SELECT id, %s FROM listing WHERE {where}"
        " ON CONFLICT (user_id, listing_id) DO UPDATE SET matched = EXCLUDED.matched",
        (matched,),
    )


def status(
    sql: psycopg.Connection[Any], value: str, where: str = "true", params: tuple[Any, ...] = ()
) -> None:
    """The owner's status for these listings (all of them)."""
    sql.execute(
        f"INSERT INTO user_listing (listing_id, status) SELECT id, %s FROM listing WHERE {where}"
        " ON CONFLICT (user_id, listing_id) DO UPDATE SET status = EXCLUDED.status",
        (value, *params),
    )
