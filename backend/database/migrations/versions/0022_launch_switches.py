"""The admin's per-account switch for Automatic mode (launch decision D2).

Automatic mode submits an application without the person seeing it first. For the launch it
is the admin's to allow, account by account: app_user.may_automatic, off for everyone. The
admin's own account is not held to it (pipeline/limits.py: automatic_allowed). An account
already set to Automatic without the switch goes back to Review, so nothing is sent on a
mode nobody allowed.

The other two launch decisions need no schema: AI credit is app_user.limit_ai_usd, which
migration 0014_account_limits added, and the invite list is an app_setting row.

Revision ID: 0022_launch_switches
Revises: 0021_billing
Create Date: 2026-11-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022_launch_switches"
down_revision: str | None = "0021_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE app_user ADD COLUMN may_automatic boolean NOT NULL DEFAULT false")
    op.execute(
        """
        UPDATE profile SET apply_mode = 'review'
         WHERE apply_mode = 'automatic'
           AND user_id IN (SELECT id FROM app_user WHERE NOT is_admin)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app_user DROP COLUMN may_automatic")
