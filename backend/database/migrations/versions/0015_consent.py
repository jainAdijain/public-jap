"""What a user agreed to before JAP acts for them (PRD S14).

Three separate agreements, because they are three different things to agree to:

- app_user.terms_accepted_at and terms_version: the terms and the privacy notice, ticked at
  sign-up. The version is kept so a later notice can be shown again rather than assumed.
- profile.submit_opt_in_at: JAP may open an application form and press Submit in this
  person's name. Nothing is sent without it.
- profile.auto_opt_in_at: JAP may do that without asking first (Automatic mode).

Nothing is backfilled. Accounts that exist already are asked once on their next visit: a
consent written by a migration is one nobody gave.

Revision ID: 0015_consent
Revises: 0014_account_limits
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015_consent"
down_revision: str | None = "0014_account_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE app_user
          ADD COLUMN terms_accepted_at timestamptz,
          ADD COLUMN terms_version text;

        ALTER TABLE profile
          ADD COLUMN submit_opt_in_at timestamptz,
          ADD COLUMN auto_opt_in_at timestamptz;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE profile DROP COLUMN auto_opt_in_at, DROP COLUMN submit_opt_in_at;
        ALTER TABLE app_user DROP COLUMN terms_version, DROP COLUMN terms_accepted_at;
        """
    )
