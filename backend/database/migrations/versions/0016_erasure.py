"""What has to give way before an account can really be deleted (PRD S15).

Delete my account means every private row goes, and two rules written for a single-user JAP
stand in the way:

- application_frozen refuses to delete an Applied application (A2). That rule exists so no
  code path can quietly rewrite the record of what was sent; it was never meant to keep a
  person's data after they asked for it to be gone. The function gains one escape hatch,
  app.erasing, which only pipeline/erasure.py sets, and only inside the transaction that
  deletes the account. Everything else still gets the exception.
  Replacing the function is the narrow way to do it: DISABLE TRIGGER would take an
  exclusive lock on the whole application table and stop every other user meanwhile.
- llm_call.billed_to allows system, user and byok. The rows stay after a deletion with
  their user_id nulled, because they hold no personal content (purpose, model, cost) and
  they are what the OpenRouter bill is counted from; deleting them would rewrite the
  service's own accounts. They need a value saying the account they paid for is gone.

Revision ID: 0016_erasure
Revises: 0015_consent
Create Date: 2026-11-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_erasure"
down_revision: str | None = "0015_consent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Set with SET LOCAL, so it lasts the transaction and no longer (pipeline/erasure.py).
# COALESCE is not decoration: unset, current_setting(..., true) is NULL, and the guard below
# reads `applied AND NOT (NULL)`, which is NULL, not true — the rule would quietly stop
# firing for everybody. tests/test_account_delete.py fails if this is written the short way.
ERASING = "COALESCE(current_setting('app.erasing', true), '') = 'on'"

FROZEN_WITH_ERASURE = f"""
CREATE OR REPLACE FUNCTION application_frozen() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status = 'applied' AND NOT ({ERASING}) THEN
      RAISE EXCEPTION 'application % is Applied and cannot be deleted', OLD.id;
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status = 'applied' AND
     (to_jsonb(NEW) - 'email_sent_at' - 'updated_at')
       IS DISTINCT FROM (to_jsonb(OLD) - 'email_sent_at' - 'updated_at') THEN
    RAISE EXCEPTION 'application % is Applied and frozen', OLD.id;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
"""

FROZEN_AS_BEFORE = """
CREATE OR REPLACE FUNCTION application_frozen() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status = 'applied' THEN
      RAISE EXCEPTION 'application % is Applied and cannot be deleted', OLD.id;
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status = 'applied' AND
     (to_jsonb(NEW) - 'email_sent_at' - 'updated_at')
       IS DISTINCT FROM (to_jsonb(OLD) - 'email_sent_at' - 'updated_at') THEN
    RAISE EXCEPTION 'application % is Applied and frozen', OLD.id;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(FROZEN_WITH_ERASURE)
    op.execute(
        """
        ALTER TABLE llm_call DROP CONSTRAINT llm_call_billed_to_check;
        ALTER TABLE llm_call ADD CONSTRAINT llm_call_billed_to_check
          CHECK (billed_to IN ('system', 'user', 'byok', 'deleted'));
        """
    )


def downgrade() -> None:
    op.execute(FROZEN_AS_BEFORE)
    op.execute(
        """
        UPDATE llm_call SET billed_to = 'system' WHERE billed_to = 'deleted';
        ALTER TABLE llm_call DROP CONSTRAINT llm_call_billed_to_check;
        ALTER TABLE llm_call ADD CONSTRAINT llm_call_billed_to_check
          CHECK (billed_to IN ('system', 'user', 'byok'));
        """
    )
