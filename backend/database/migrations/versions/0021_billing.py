"""Paid plans: one subscription per account, and the provider events that move it (IDEA-21).

The PRD is absent from this checkout, so there is no PRD id for paid plans yet. The idea is
IDEA-21 in docs/ideas.md; replace the citation here and in backend/billing/ with the PRD id once
the section exists.

subscription is an account's side of a plan bought from a payment provider: which plan, what
state it is in, and the end of the period it has paid for. It is private data — a user_id,
a policy, and an entry in database/tenant.PRIVATE_TABLES — because it is that account's own
and nobody else's. The webhook writes it as the service, which is the table owner and so is
not held by the policy.

One live subscription an account: the partial unique index covers exactly the states that
grant a plan, so a second checkout cannot start while one is pending or running. Cancelled
and expired rows stay as history, which is why the index is partial and not a constraint on
user_id.

state_at is the provider's own time for the state the row holds. Providers retry webhooks
and deliver them out of order, so an event older than state_at is read and discarded rather
than applied; without it a late subscription.pending could put a live account back into
arrears.

billing_event carries no user_id on purpose. It is the record that an event was handled, and
events arrive for subscriptions this database has never heard of; a user_id would also make
it a private table (tests/test_tenancy.py) and so invisible to the webhook's own insert.
UNIQUE (provider, event_id) is what makes "exactly once" a database matter rather than a
code one. It holds the few fields the handler acts on and never the raw body, which carries
the payer's name, email and billing address.

Revision ID: 0021_billing
Revises: 0020_pacing
Create Date: 2026-11-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_billing"
down_revision: str | None = "0020_pacing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must stay equal to database.tenant.USER_ID_SQL (migration 0013_rls says why).
USER_ID = "(NULLIF(current_setting('app.user_id', true), '')::uuid)"
ROLE = "jap_app"

# The states that grant the account its plan. past_due is one of them: a failed renewal must
# not take the plan away on the day it failed (pipeline/limits.py).
LIVE = "('pending', 'active', 'past_due')"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE subscription (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL DEFAULT {USER_ID} REFERENCES app_user (id),
          provider text NOT NULL CHECK (provider IN ('razorpay')),
          provider_ref text NOT NULL,
          plan_code text NOT NULL,
          status text NOT NULL
            CHECK (status IN ('pending', 'active', 'past_due', 'cancelled', 'expired')),
          current_period_end timestamptz,
          cancel_at_period_end boolean NOT NULL DEFAULT false,
          state_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (provider, provider_ref)
        );
        CREATE UNIQUE INDEX subscription_one_live ON subscription (user_id)
          WHERE status IN {LIVE};
        CREATE TRIGGER subscription_touch BEFORE UPDATE ON subscription
          FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

        ALTER TABLE subscription ENABLE ROW LEVEL SECURITY;
        CREATE POLICY subscription_owner ON subscription TO {ROLE}
          USING (user_id = {USER_ID}) WITH CHECK (user_id = {USER_ID});

        CREATE TABLE billing_event (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          provider text NOT NULL,
          event_id text NOT NULL,
          event_type text NOT NULL,
          occurred_at timestamptz,
          provider_ref text,
          status text,
          amount numeric(12, 2),
          currency text,
          handled text,
          received_at timestamptz NOT NULL DEFAULT now(),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (provider, event_id)
        );
        CREATE INDEX billing_event_ref ON billing_event (provider, provider_ref, received_at);
        CREATE TRIGGER billing_event_touch BEFORE UPDATE ON billing_event
          FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE billing_event;
        DROP POLICY subscription_owner ON subscription;
        DROP TABLE subscription;
        """
    )
