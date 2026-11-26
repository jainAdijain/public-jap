# Plans, limits and money

**Requirements:** BILL-1 to BILL-6, APPLY-6, APPLY-8 ([requirements.md](../intent/requirements.md)). The tests that
prove each one are in [traceability.md](traceability.md); a rule here with no test is a gap
there.

Code: `backend/pipeline/limits.py` (the only place that decides), `backend/llm/spend.py`,
`backend/billing/`, `backend/api/billing.py`, `backend/api/webhooks.py`, migrations
`0014_account_limits`, `0021_billing`. Long form: `README.md` (Sign-in and accounts).

## What a limit is

Every account has three numbers:

| Number | Free default | Plus default | Setting |
| --- | --- | --- | --- |
| Companies followed | 1 | 10 | `JAP_FREE_BOARDS`, `JAP_PLUS_BOARDS` |
| Applications a day | 1 | 10 | `JAP_FREE_DAILY_APPLICATIONS`, `JAP_PLUS_DAILY_APPLICATIONS` |
| AI spend a month on JAP's key | $0 | $0 | `JAP_MONTHLY_COST_CAP_USD` (both plans) |

The admin is on no plan: no company limit, 100 applications a day, and AI bounded only by
the total (`JAP_TOTAL_COST_CAP_USD`) and their own setting.

## Where an account's AI comes from (launch decision D1)

No plan includes AI on JAP's key. An account's AI comes from one of two places, and the
screens say which (`ai_source`: `own_key`, `jap` or `none`):

1. **Its own OpenRouter key** (below). Used first whenever one is saved.
2. **Credit the admin grants it**: the "AI credit a month" box in Settings > Accounts, which
   is the account's `limit_ai_usd`. It is the admin's money on the admin's key.

With neither, `llm.call()` refuses before any model is asked (`NoAiCredit`, a
`SpendCapReached`), with "This account has no AI credit: add your own OpenRouter key in
Settings, or ask the admin for credit". Jobs and Settings show a banner saying the same.
Finding and matching jobs still work: embeddings are local. Reading shared job descriptions
is the service's own budget, whoever follows the company. Using a person's Claude or
ChatGPT subscription instead is not done: those terms do not allow it (IDEA-25).

## How `limits_for` resolves them

The order is the whole policy:

1. a number the **admin set for this one account** (`app_user.limit_*`) beats everything;
2. otherwise the **plan** the account's subscription grants, else free;
3. on top of either, what the **owner chose** in Settings (AI limit, daily cap), which can
   only lower a figure, never raise it.

`api/boards.py`, `pipeline/apply/flow.py` and `llm/spend.py` ask `limits_for` and never learn
that subscriptions exist. A plan code that no longer exists falls back to free, never to "no
limits".

## The three AI budgets

| Budget | Pays for | Setting (default) |
| --- | --- | --- |
| Each account's allowance | work for that account: parsing its resume, drafting answers, cover letters | per plan, above |
| The service's budget | reading shared job descriptions (nobody's in particular) | `JAP_SYSTEM_COST_CAP_USD` ($5) |
| The total | everything together; bounds the OpenRouter bill | `JAP_TOTAL_COST_CAP_USD` ($20) |

One account reaching its allowance stops only that account. The total stops everyone. Every
call is an `llm_call` row with its cost and `billed_to`. An application costs about 2 to 3
cents of AI. A description is read only when its title is a target role of someone who follows
that company, and only once per description.

**Your own key** (IDEA-19): an account can paste an OpenRouter key; its work is then billed to
it (`billed_to='byok'`) and left out of its allowance, the service budget and the total. The
key is sealed with AES-GCM under `JAP_SECRET_KEY`, bound to the account; only its last four
characters are ever shown. The card is hidden while `JAP_SECRET_KEY` is unset.

The PR 6 load test found 150 accounts at $1 each could spend $155 against a $20 total.
Since v0.25.0 the worst case is the $5 service budget plus whatever the admin has granted
(`make load-test` still multiplies accounts by `JAP_MONTHLY_COST_CAP_USD`, now 0).

## Plus and billing (IDEA-21)

- **Off by default**, and staying off until real users have tried JAP (launch decision D3):
  `JAP_BILLING_ENABLED=false` means nothing is for sale and there is no Upgrade button.
- **Plus sells companies and applications, not AI.** Its allowance on JAP's key is the same
  $0 as free; `JAP_PLUS_AI_USD` was removed in v0.25.0.
- **Checkout is a redirect** to a page Razorpay hosts. No card detail reaches JAP, no payment
  script runs on its pages, the CSP is unchanged, and the subscription carries only the
  account's id.
- **The webhook** `/api/webhooks/razorpay` trusts only an HMAC over the raw body; unsigned is
  404, and so is everything while no provider is configured. Applied exactly once
  (`UNIQUE (provider, event_id)`, inserted before acting, same transaction), never backwards
  (an event older than the state held is recorded and dropped), run as the system tenant.
- **Subscription states**: `pending` grants nothing; `active` and `past_due` grant the plan.
  A failed payment keeps the plan to the end of the paid period plus
  `JAP_BILLING_GRACE_DAYS` (3). Cancelling stops renewal, the period runs out.
- **Deleting an account cancels the mandate first** (best effort, never blocking the delete).
- Price shown is `JAP_PLUS_PRICE_INR` (499) / `JAP_PLUS_PRICE_USD` (9.0), for display only:
  what is charged lives at Razorpay and must be kept in step by hand.
- `subscription` is a private table, so export and erasure carry it. `billing_event` has no
  `user_id` and never stores the raw body.

**Not done**: a merchant of record for outside India; and nothing may be sold until terms,
privacy and refund pages exist and the seller's tax position is settled. See
[launch-plan.md](../intent/launch-plan.md), decision D3.

## Automatic mode (launch decision D2)

Automatic mode is the admin's to allow, account by account: `app_user.may_automatic`, off by
default, switched with "Allow Automatic" in Settings > Accounts
(`PUT /api/admin/accounts/{id}/automatic`). `automatic_allowed()` in `pipeline/limits.py` is
the one place that answers it; the admin's own account always may.

- Without it, choosing Automatic is refused (403 `automatic_not_allowed`) and the option is
  greyed out with "Not open to this account yet".
- Taking it away puts the account back on Review mode at once. A job already prepared keeps the
  mode it was prepared in.
- A job is prepared in Review mode whenever the account is not allowed, whatever its profile
  says (`flow.create_attempt`), so an old row cannot send on a mode nobody allowed. Migration
  `0022_launch_switches` moved every non-admin account on Automatic back to Review.

## Other per-account and service-wide limits on applying

| Limit | Setting (default) |
| --- | --- |
| Gap between one account's submissions | `JAP_APPLY_GAP_SECONDS` (30) |
| Gap between submissions to one job site, across everyone | `JAP_APPLY_ATS_GAP_SECONDS` (60) |
| Submissions a day across everyone | `JAP_DAILY_APPLICATIONS_TOTAL` (200) |
| Browsers applying at once (one per account) | `JAP_APPLY_BROWSERS` (4) |
| Accounts searching at once (one search per account) | `JAP_MAX_PARALLEL_RUNS` (3) |
| Pause sending | per account, in Settings; neither consent withdrawn nor account off |
