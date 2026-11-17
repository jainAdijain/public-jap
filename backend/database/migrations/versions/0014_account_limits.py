"""Limits the admin sets for one account (PRD S12).

Every account is on the free plan, whose limits come from .env (pipeline/limits.py). These
three columns hold what the admin chose for one account instead: AI spend a month, companies
followed, applications a day. Null means the plan's own number.

Revision ID: 0014_account_limits
Revises: 0013_rls
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_account_limits"
down_revision: str | None = "0013_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE app_user
          ADD COLUMN limit_ai_usd numeric(8,2) CHECK (limit_ai_usd >= 0),
          ADD COLUMN limit_boards integer CHECK (limit_boards >= 0),
          ADD COLUMN limit_applications integer CHECK (limit_applications BETWEEN 0 AND 100);
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE app_user DROP COLUMN limit_applications, DROP COLUMN limit_boards,"
        " DROP COLUMN limit_ai_usd"
    )
