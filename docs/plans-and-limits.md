# Plans, limits and money

Code: `backend/pipeline/limits.py` (the only place that decides), `backend/llm/spend.py`,
`backend/billing/`, `backend/api/billing.py`, `backend/api/webhooks.py`, migrations
`0014_account_limits`, `0021_billing`. Long form: `README.md` (Sign-in and accounts).

## What a limit is

Every account has three numbers:

| Number | Free default | Plus default | Setting |
| --- | --- | --- | --- |
| Companies followed | 1 | 10 | `JAP_FREE_BOARDS`, `JAP_PLUS_BOARDS` |
| Applications a day | 1 | 10 | `JAP_FREE_DAILY_APPLICATIONS`, `JAP_PLUS_DAILY_APPLICATIONS` |
| AI spend a month on JAP's key | $1 | $8 | `JAP_MONTHLY_COST_CAP_USD`, `JAP_PLUS_AI_USD` |

The admin is on no plan: no company limit, 100 applications a day, and their own AI setting.

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

> **Known problem before launch** (PR 6 load test): 150 free accounts can spend $150 a month
> plus $5 for descriptions, against a $20 total. Decision D1 in
> [launch-plan.md](launch-plan.md).

## Plus and billing (IDEA-21)

- **Off by default**: `JAP_BILLING_ENABLED=false` means nothing is for sale and there is no
  Upgrade button.
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
[launch-plan.md](launch-plan.md), decision D3.

## Other per-account and service-wide limits on applying

| Limit | Setting (default) |
| --- | --- |
| Gap between one account's submissions | `JAP_APPLY_GAP_SECONDS` (30) |
| Gap between submissions to one job site, across everyone | `JAP_APPLY_ATS_GAP_SECONDS` (60) |
| Submissions a day across everyone | `JAP_DAILY_APPLICATIONS_TOTAL` (200) |
| Browsers applying at once (one per account) | `JAP_APPLY_BROWSERS` (4) |
| Accounts searching at once (one search per account) | `JAP_MAX_PARALLEL_RUNS` (3) |
| Pause sending | per account, in Settings; neither consent withdrawn nor account off |
