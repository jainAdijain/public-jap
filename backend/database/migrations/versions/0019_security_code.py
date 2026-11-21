"""A security code for every user, not only the admin (IDEA-16).

Greenhouse emails an 8-character code before it takes an application (A5). JAP could only
read the one mailbox it owns, so only the admin's applications could finish: everyone else
was told to apply by hand. Three ways in, and a form takes whichever arrives first:

- app_user.code_token: the account's own forwarding address, jap+<token>@..., set once by
  a mail filter. A code is matched by the address it was sent to as well as the sender, so
  two people waiting at the same moment cannot be handed each other's code. This is what
  makes applying side by side safe (IDEA-15), and it is why this lands with it.
- The same token on an inbound-email service's webhook, for mail providers that do not
  keep a usable recipient header through a forward.
- application.security_code: the person reads the code and types it in. Always available,
  and the only route on a stack with no mailbox at all.

application.code_wanted_at is what the screen watches to know it should ask. The code
itself is held on the row only until the form takes it, and the same statement that marks
the application Applied clears it; nothing logs it (observability/scrub.py drops the field
name) and it is left out of an export (pipeline/erasure.py).

Revision ID: 0019_security_code
Revises: 0018_user_secret
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_security_code"
down_revision: str | None = "0018_user_secret"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE app_user ADD COLUMN code_token text;
        CREATE UNIQUE INDEX app_user_code_token ON app_user (code_token)
          WHERE code_token IS NOT NULL;

        ALTER TABLE application
          ADD COLUMN code_wanted_at timestamptz,
          ADD COLUMN security_code text;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE application DROP COLUMN security_code, DROP COLUMN code_wanted_at;
        DROP INDEX app_user_code_token;
        ALTER TABLE app_user DROP COLUMN code_token;
        """
    )
