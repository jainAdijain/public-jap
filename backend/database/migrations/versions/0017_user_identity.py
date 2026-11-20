"""Signing in with Google as well as with a password (PRD S16).

user_identity is the link between a JAP account and an account somewhere else: one row per
provider per user, found by (provider, subject). The subject is the provider's own id for
that person, never their email, because an email can be given to somebody else later and a
subject cannot.

email on the row is what the provider said at the time, kept for the admin to recognise and
never used to find the row.

password_hash stays NOT NULL. An account with no password of its own carries the empty
string, which is what the placeholder owner from 0011_tenancy already uses, and which
auth/passwords.py already answers as a wrong password rather than as a match. A nullable
column would have made every caller handle None for no gain.

Revision ID: 0017_user_identity
Revises: 0016_erasure
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_user_identity"
down_revision: str | None = "0016_erasure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE user_identity (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL REFERENCES app_user (id),
          provider text NOT NULL CHECK (provider IN ('google')),
          subject text NOT NULL,
          email text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (provider, subject),
          UNIQUE (user_id, provider)
        );
        CREATE TRIGGER user_identity_touch BEFORE UPDATE ON user_identity
          FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE user_identity")
