"""Open sign-up (PRD 1.18 step 2: S1, S10, S12).

Anyone can create an account, so three things come with it:

- email_verification: the emailed link a new account must open before it can sign in (S10),
  single use and 24 hours, kept as a hash like a session or a reset link.
- rate_limit: tries per key in a time window, for sign-up, forgot password and "send the link
  again" (auth/limits.py). The key is a hash of the visitor's address or of the email.
- app_setting: what the admin switches for the whole service; today sign_up_paused (S12).

app_user.last_sign_in_at is for the admin's list of accounts (S12).

Revision ID: 0012_email_verify
Revises: 0011_tenancy
Create Date: 2026-11-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_email_verify"
down_revision: str | None = "0011_tenancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE email_verification (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
            token_hash text NOT NULL UNIQUE,
            expires_at timestamptz NOT NULL,
            used_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX email_verification_user ON email_verification (user_id, created_at DESC);

        CREATE TABLE rate_limit (
            key text NOT NULL,
            window_start timestamptz NOT NULL,
            count integer NOT NULL DEFAULT 0,
            PRIMARY KEY (key, window_start)
        );
        CREATE INDEX rate_limit_window ON rate_limit (window_start);

        CREATE TABLE app_setting (
            key text PRIMARY KEY,
            value jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        );

        ALTER TABLE app_user ADD COLUMN last_sign_in_at timestamptz;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE app_user DROP COLUMN last_sign_in_at;
        DROP TABLE app_setting;
        DROP TABLE rate_limit;
        DROP TABLE email_verification;
        """
    )
