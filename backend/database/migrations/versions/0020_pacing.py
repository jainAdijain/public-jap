"""Pacing submissions across every account, not only within one (IDEA-17).

Each account already waits 30 seconds between its own applications and stops at its daily
cap (A1). Neither says anything about the service as a whole, and with people applying
side by side (IDEA-15) it is the whole that a job site sees: fifty accounts each politely
spaced still arrive as fifty applications a minute from one address.

So three limits that span accounts, and a switch:

- jap_last_submit(ats): when anyone last submitted to this job site, for a gap per site.
- jap_applied_today(since): how many applications the service has sent since then, for a
  ceiling under every account's own cap.
- profile.submit_paused: one account's sending stopped without turning the account off or
  withdrawing its consent. The global JAP_SUBMIT_ENABLED stays what it was.

Both functions run as the owner and return one number, never a row, exactly as jap_spend
does (0013_rls): a user's session must not be able to read other people's applications,
but it has to be able to ask how busy the service is.

Revision ID: 0020_pacing
Revises: 0019_security_code
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_pacing"
down_revision: str | None = "0019_security_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "jap_app"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE profile ADD COLUMN submit_paused boolean NOT NULL DEFAULT false;

        CREATE FUNCTION jap_last_submit(which text) RETURNS timestamptz
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT max(a.submitted_at) FROM application a JOIN posting p ON p.id = a.posting_id
          WHERE a.status = 'applied' AND p.source::text = which
        $$;
        REVOKE ALL ON FUNCTION jap_last_submit(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION jap_last_submit(text) TO {ROLE};

        CREATE FUNCTION jap_applied_today(since timestamptz) RETURNS bigint
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT count(*) FROM application
          WHERE status = 'applied' AND submitted_at >= since
        $$;
        REVOKE ALL ON FUNCTION jap_applied_today(timestamptz) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION jap_applied_today(timestamptz) TO {ROLE};
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP FUNCTION jap_applied_today(timestamptz);
        DROP FUNCTION jap_last_submit(text);
        ALTER TABLE profile DROP COLUMN submit_paused;
        """
    )
