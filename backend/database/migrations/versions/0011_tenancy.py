"""Each user's own data (PRD 1.18 step 2: S7, S13).

Facts about jobs stay shared: company, watched_board, posting, listing and the dedup tables
have no owner, so a board two people follow is fetched and read once. Everything about a
person gets a user_id: profile (one per account, in place of the single row), resume_version,
answer_bank, application and run. Two new tables hold the personal half of what used to sit
on shared rows:

- board_follow: who follows which board. watched_board.active now means "someone follows it",
  which is what decides whether it is fetched.
- user_listing: what a listing is to one user (matched, the skills match, status,
  applied_at). Those columns leave listing, which keeps only facts and closed_at.

llm_call says who a model call was for (user_id, null for reading shared postings) and
whose allowance paid (billed_to). alert_state says whose alert it was.

Every row that exists now belongs to the admin (S1). A database with data but no account
yet (one from before sign-in) gets a placeholder account that the first sign-up takes over
(api/auth.py).

Revision ID: 0011_tenancy
Revises: 0010_skills_match
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_tenancy"
down_revision: str | None = "0010_skills_match"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must stay equal to database.tenant.USER_ID_SQL and auth.accounts.UNCLAIMED_EMAIL.
USER_ID = "(NULLIF(current_setting('app.user_id', true), '')::uuid)"
UNCLAIMED = "unclaimed@jap.invalid"
OWNED = ("resume_version", "answer_bank", "application", "run")
PERSONAL = ("answer", "check", "claim_check", "cover_letter", "resume_parse")


def _owner() -> str | None:
    """The account today's rows go to: the first admin, or a placeholder when there is data
    and nobody to give it to. None on an empty database."""
    bind = op.get_bind()
    owner = bind.execute(
        sa.text("SELECT id FROM app_user ORDER BY is_admin DESC, created_at LIMIT 1")
    ).scalar()
    if owner is not None:
        return str(owner)
    has_data = bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM profile) OR EXISTS (SELECT 1 FROM resume_version)"
            " OR EXISTS (SELECT 1 FROM answer_bank) OR EXISTS (SELECT 1 FROM application)"
            " OR EXISTS (SELECT 1 FROM run) OR EXISTS (SELECT 1 FROM watched_board)"
            " OR EXISTS (SELECT 1 FROM listing)"
        )
    ).scalar()
    if not has_data:
        return None
    return str(
        bind.execute(
            sa.text(
                "INSERT INTO app_user (email, password_hash, is_admin, email_verified_at)"
                " VALUES (:email, '', true, now()) RETURNING id"
            ),
            {"email": UNCLAIMED},
        ).scalar()
    )


def upgrade() -> None:
    owner = _owner()
    bind = op.get_bind()

    # Today's rows get the owner through the column's default, not an UPDATE: an Applied
    # application is frozen (application_frozen), and a default fires no trigger.
    first = f" DEFAULT '{owner}'" if owner is not None else ""
    for table in ("profile", *OWNED):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN user_id uuid{first} REFERENCES app_user (id)"
        )
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN user_id SET NOT NULL,"
            f" ALTER COLUMN user_id SET DEFAULT {USER_ID}"
        )

    op.execute(
        """
        ALTER TABLE profile DROP COLUMN singleton, ADD COLUMN tz text,
          ADD CONSTRAINT profile_user_id_key UNIQUE (user_id);

        DROP INDEX one_active_resume;
        CREATE UNIQUE INDEX one_active_resume ON resume_version (user_id) WHERE is_active;
        CREATE INDEX resume_version_user ON resume_version (user_id, uploaded_at DESC);

        ALTER TABLE answer_bank DROP CONSTRAINT answer_bank_question_norm_key,
          ADD CONSTRAINT answer_bank_user_question_key UNIQUE (user_id, question_norm);

        DROP INDEX one_applied_per_listing;
        DROP INDEX one_active_attempt;
        CREATE UNIQUE INDEX one_applied_per_listing ON application (user_id, listing_id)
          WHERE status = 'applied';
        CREATE UNIQUE INDEX one_active_attempt ON application (user_id, listing_id)
          WHERE status IN ('preparing','ready_for_review','applying','needs_input');
        CREATE INDEX application_user ON application (user_id, created_at DESC);

        -- One queued run each (T4); runs still take turns across the service.
        DROP INDEX one_queued_run;
        CREATE UNIQUE INDEX one_queued_run ON run (user_id) WHERE status = 'queued';
        CREATE INDEX run_user ON run (user_id, created_at DESC);

        ALTER TABLE llm_call
          ADD COLUMN user_id uuid REFERENCES app_user (id),
          ADD COLUMN billed_to text NOT NULL DEFAULT 'system'
            CHECK (billed_to IN ('system', 'user', 'byok'));
        CREATE INDEX llm_call_user_month ON llm_call (user_id, created_at);

        ALTER TABLE alert_state ADD COLUMN user_id uuid REFERENCES app_user (id);

        CREATE TABLE board_follow (
          user_id uuid NOT NULL REFERENCES app_user (id),
          board_id uuid NOT NULL REFERENCES watched_board (id),
          active boolean NOT NULL DEFAULT true,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, board_id)
        );
        CREATE INDEX board_follow_board ON board_follow (board_id) WHERE active;
        CREATE TRIGGER board_follow_touch BEFORE UPDATE ON board_follow
          FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

        CREATE TABLE user_listing (
          user_id uuid NOT NULL REFERENCES app_user (id),
          listing_id uuid NOT NULL REFERENCES listing (id) ON DELETE CASCADE,
          status listing_status NOT NULL DEFAULT 'not_applied',
          matched text[],
          matched_role text,
          required_hit text[] NOT NULL DEFAULT '{}',
          nice_hit text[] NOT NULL DEFAULT '{}',
          required_pct smallint CHECK (required_pct BETWEEN 0 AND 100),
          applied_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, listing_id)
        );
        CREATE INDEX user_listing_listing ON user_listing (listing_id);
        CREATE INDEX user_listing_dashboard ON user_listing (user_id, status)
          WHERE matched IS NOT NULL;
        CREATE TRIGGER user_listing_touch BEFORE UPDATE ON user_listing
          FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
        """
    )
    op.execute(f"ALTER TABLE board_follow ALTER COLUMN user_id SET DEFAULT {USER_ID}")
    op.execute(f"ALTER TABLE user_listing ALTER COLUMN user_id SET DEFAULT {USER_ID}")

    if owner is not None:
        params = {"owner": owner, "personal": list(PERSONAL)}
        bind.execute(
            sa.text(
                "INSERT INTO board_follow (user_id, board_id, active)"
                " SELECT :owner, id, active FROM watched_board"
            ),
            params,
        )
        bind.execute(
            sa.text(
                "INSERT INTO user_listing (user_id, listing_id, status, matched, matched_role,"
                " required_hit, nice_hit, required_pct, applied_at, created_at)"
                " SELECT :owner, id, status, matched, matched_role, required_hit, nice_hit,"
                " required_pct, applied_at, created_at FROM listing"
                " WHERE matched IS NOT NULL OR status NOT IN ('not_applied', 'closed')"
            ),
            params,
        )
        bind.execute(
            sa.text(
                "UPDATE llm_call SET user_id = :owner, billed_to = 'user'"
                " WHERE purpose = ANY(:personal)"
            ),
            params,
        )

    op.execute(
        """
        -- closed_at becomes a fact about the job: when its last open posting closed.
        UPDATE listing l SET closed_at = c.at
        FROM (SELECT listing_id, max(closed_at) AS at FROM posting
              GROUP BY listing_id HAVING bool_and(state = 'closed')) c
        WHERE c.listing_id = l.id AND l.closed_at IS NULL;
        UPDATE listing l SET closed_at = NULL
        WHERE closed_at IS NOT NULL
          AND EXISTS (SELECT 1 FROM posting p WHERE p.listing_id = l.id AND p.state = 'open');

        ALTER TABLE listing
          DROP COLUMN status, DROP COLUMN matched, DROP COLUMN matched_role,
          DROP COLUMN required_hit, DROP COLUMN nice_hit, DROP COLUMN required_pct,
          DROP COLUMN applied_at;
        """
    )


def downgrade() -> None:
    """Back to one person's data: the admin's rows are kept, everyone else's are dropped."""
    op.execute(
        """
        ALTER TABLE listing
          ADD COLUMN status listing_status NOT NULL DEFAULT 'not_applied',
          ADD COLUMN matched text[], ADD COLUMN matched_role text,
          ADD COLUMN required_hit text[] NOT NULL DEFAULT '{}',
          ADD COLUMN nice_hit text[] NOT NULL DEFAULT '{}',
          ADD COLUMN required_pct smallint CHECK (required_pct BETWEEN 0 AND 100),
          ADD COLUMN applied_at timestamptz;
        UPDATE listing SET status = 'closed' WHERE closed_at IS NOT NULL;
        UPDATE listing l SET status = u.status, matched = u.matched,
          matched_role = u.matched_role, required_hit = u.required_hit, nice_hit = u.nice_hit,
          required_pct = u.required_pct, applied_at = u.applied_at
        FROM user_listing u
        WHERE u.listing_id = l.id AND u.user_id =
          (SELECT id FROM app_user ORDER BY is_admin DESC, created_at LIMIT 1);
        CREATE INDEX listing_dashboard ON listing (status, last_posted_at DESC)
          WHERE matched IS NOT NULL;
        CREATE INDEX listing_matched_role ON listing (matched_role) WHERE matched IS NOT NULL;
        DROP TABLE user_listing;
        DROP TABLE board_follow;

        ALTER TABLE alert_state DROP COLUMN user_id;
        ALTER TABLE llm_call DROP COLUMN billed_to, DROP COLUMN user_id;
        """
    )
    op.execute("ALTER TABLE application DISABLE TRIGGER application_frozen")
    op.execute(
        "DELETE FROM llm_call WHERE run_id IN (SELECT id FROM run WHERE user_id <>"
        " (SELECT id FROM app_user ORDER BY is_admin DESC, created_at LIMIT 1))"
    )
    for table in ("application", "run", "answer_bank", "resume_version", "profile"):
        op.execute(
            f"DELETE FROM {table} WHERE user_id <>"
            " (SELECT id FROM app_user ORDER BY is_admin DESC, created_at LIMIT 1)"
        )
    op.execute("ALTER TABLE application ENABLE TRIGGER application_frozen")
    op.execute(
        """
        DROP INDEX one_queued_run;
        CREATE UNIQUE INDEX one_queued_run ON run ((true)) WHERE status = 'queued';
        DROP INDEX one_applied_per_listing;
        DROP INDEX one_active_attempt;
        CREATE UNIQUE INDEX one_applied_per_listing ON application (listing_id)
          WHERE status = 'applied';
        CREATE UNIQUE INDEX one_active_attempt ON application (listing_id)
          WHERE status IN ('preparing','ready_for_review','applying','needs_input');
        ALTER TABLE answer_bank DROP CONSTRAINT answer_bank_user_question_key,
          ADD CONSTRAINT answer_bank_question_norm_key UNIQUE (question_norm);
        DROP INDEX one_active_resume;
        CREATE UNIQUE INDEX one_active_resume ON resume_version (is_active) WHERE is_active;
        ALTER TABLE profile DROP COLUMN tz,
          ADD COLUMN singleton boolean NOT NULL DEFAULT true UNIQUE CHECK (singleton);
        """
    )
    for table in ("profile", *OWNED):
        op.execute(f"ALTER TABLE {table} DROP COLUMN user_id")
