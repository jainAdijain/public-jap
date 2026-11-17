"""A second lock on each user's data, in Postgres itself (PRD S7).

The code already filters every private query by user. This makes the database refuse too,
so a query that one day forgets its filter returns nothing instead of someone else's rows.

- jap_app: a role that owns nothing and cannot sign in. The app's sessions switch to it at
  the start of every transaction (database/session.py), with app.user_id set to the user
  the work is for.
- Row-level security on every private table (profile, resume_version, answer_bank,
  application, run, board_follow, user_listing, llm_call): jap_app sees and writes only rows
  whose user_id is app.user_id. With app.user_id unset, that is no rows at all.
- The table owner is not subject to the policies. Work on shared data (fetching boards,
  reading postings, dedup, the admin's screens, migrations, make targets) says it is the
  service's (database/tenant.py) and stays the owner.
- jap_spend(since, system_only): this month's model spend as one number, for the limits
  that span users (the total, and the service's budget). It runs as the owner so any tenant
  can ask, and it returns a sum, never a row.

The role is shared by every database on the server, so it is created only if missing and
is not dropped on downgrade.

Revision ID: 0013_rls
Revises: 0012_email_verify
Create Date: 2026-11-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_rls"
down_revision: str | None = "0012_email_verify"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must stay equal to database.tenant: APP_ROLE, USER_ID_SQL and PRIVATE_TABLES.
ROLE = "jap_app"
USER_ID = "(NULLIF(current_setting('app.user_id', true), '')::uuid)"
PRIVATE = (
    "profile", "resume_version", "answer_bank", "application", "run", "board_follow",
    "user_listing", "llm_call",
)  # fmt: skip


def upgrade() -> None:
    op.execute(
        f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
            CREATE ROLE {ROLE} NOLOGIN;
          END IF;
        END $$;
        GRANT {ROLE} TO CURRENT_USER;
        GRANT USAGE ON SCHEMA public TO {ROLE};
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROLE};
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
          GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {ROLE};

        CREATE FUNCTION jap_spend(since timestamptz, system_only boolean) RETURNS numeric
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT COALESCE(sum(cost_usd), 0) FROM llm_call
          WHERE created_at >= since AND (NOT system_only OR user_id IS NULL)
        $$;
        REVOKE ALL ON FUNCTION jap_spend(timestamptz, boolean) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION jap_spend(timestamptz, boolean) TO {ROLE};
        """
    )
    for table in PRIVATE:
        op.execute(
            f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            CREATE POLICY {table}_owner ON {table} TO {ROLE}
              USING (user_id = {USER_ID}) WITH CHECK (user_id = {USER_ID});
            """
        )


def downgrade() -> None:
    for table in PRIVATE:
        op.execute(
            f"""
            DROP POLICY {table}_owner ON {table};
            ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
            """
        )
    op.execute(
        f"""
        DROP FUNCTION jap_spend(timestamptz, boolean);
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {ROLE};
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE};
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE};
        REVOKE USAGE ON SCHEMA public FROM {ROLE};
        """
    )
