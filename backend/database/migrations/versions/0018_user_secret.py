"""A user's own OpenRouter key, so their key pays for their work (IDEA-19).

user_secret holds one encrypted value per account per kind; today the only kind is the
OpenRouter key. The key itself is never stored: ciphertext and nonce are AES-GCM under a
master key that lives only in the environment (JAP_SECRET_KEY, llm/secrets.py), so a stolen
database dump is not a pile of working API keys. `hint` is the last four characters, which
is what the screen shows so a person can tell which key is set without it being readable.

It is a private table like the rest (PRD S7): a user_id defaulted from app.user_id,
row-level security on, and a policy for jap_app. tests/test_tenancy.py checks the database
agrees with database/tenant.py, and tests/test_account_delete.py checks it is exported and
deleted with the account.

jap_spend() changes with it. Work paid for with a user's own key is not on JAP's OpenRouter
bill, so billed_to = 'byok' rows are left out of the service's budget and the total cap.
They stay in llm_call, and on the user's own Insights, because the call still happened.

Revision ID: 0018_user_secret
Revises: 0017_user_identity
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_user_secret"
down_revision: str | None = "0017_user_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "jap_app"
USER_ID = "(NULLIF(current_setting('app.user_id', true), '')::uuid)"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE user_secret (
          user_id uuid NOT NULL REFERENCES app_user (id) DEFAULT {USER_ID},
          kind text NOT NULL DEFAULT 'openrouter' CHECK (kind IN ('openrouter')),
          ciphertext bytea NOT NULL,
          nonce bytea NOT NULL,
          hint text NOT NULL DEFAULT '',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, kind)
        );
        CREATE TRIGGER user_secret_touch BEFORE UPDATE ON user_secret
          FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

        ALTER TABLE user_secret ENABLE ROW LEVEL SECURITY;
        CREATE POLICY user_secret_own ON user_secret TO {ROLE}
          USING (user_id = {USER_ID}) WITH CHECK (user_id = {USER_ID});

        CREATE OR REPLACE FUNCTION jap_spend(since timestamptz, system_only boolean)
        RETURNS numeric
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT COALESCE(sum(cost_usd), 0) FROM llm_call
          WHERE created_at >= since AND billed_to <> 'byok'
            AND (NOT system_only OR user_id IS NULL)
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE user_secret;

        CREATE OR REPLACE FUNCTION jap_spend(since timestamptz, system_only boolean)
        RETURNS numeric
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT COALESCE(sum(cost_usd), 0) FROM llm_call
          WHERE created_at >= since AND (NOT system_only OR user_id IS NULL)
        $$;
        """
    )
