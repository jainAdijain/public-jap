"""Settings read from .env (see .env.example for what each one does)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://jap:jap@db:5432/jap"

    # Which environment this stack is (H3): dev, test or prod. The UI shows a DEV or TEST badge.
    jap_env: str = "dev"
    # Which half of the app this process is (H2). The same image runs twice: `api` answers
    # /api for the browser and never mounts the Inngest endpoint, `worker` runs the functions
    # and the Playwright browser. `all` does both, which is what the tests and a bare
    # `uvicorn main:app` want. Compose sets it per service, so .env's value is only the
    # default for a process started by hand.
    jap_role: str = "all"
    # Off in test (H4): an application is never submitted to a real job site from there.
    jap_submit_enabled: bool = True

    openrouter_api_key: str = ""
    # Empty means OpenRouter itself (llm/client.py); only a stand-in for testing sets it.
    openrouter_url: str = ""
    jap_model_extract: str = "openai/gpt-5.6-luna"
    jap_model_write: str = "anthropic/claude-sonnet-4.5"
    jap_model_embed: str = "nomic-embed-text"
    jap_embed_dim: int = 768
    ollama_base_url: str = "http://host.docker.internal:11434"

    # AI spend a month (PRD S12): each user's allowance, the service's own budget for
    # reading shared job postings, and the limit on everything together. The allowance is
    # $0 by default (launch decision D1): an account's AI runs on its own OpenRouter key, or
    # on credit the admin grants it in Settings > Accounts. Neither plan includes any.
    jap_monthly_cost_cap_usd: float = 0.0
    jap_system_cost_cap_usd: float = 5.0
    jap_total_cost_cap_usd: float = 20.0
    # The free plan (pipeline/limits.py): companies one account may follow, and applications
    # it may send a day. The admin is not held to either, and can set any one account's own
    # numbers in Settings > Accounts. An application costs about 2 to 3 cents of AI, so $1
    # of credit covers about one a day for a month.
    jap_free_boards: int = 1
    jap_free_daily_applications: int = 1
    # The master key that seals a user's own OpenRouter key (IDEA-19, llm/secrets.py):
    # 32 bytes, base64. Empty means nobody can save one and the screen says so. Losing it
    # loses every saved key, and users paste theirs again.
    jap_secret_key: str = ""

    # The Plus plan (IDEA-21), the one thing that is sold: more companies and applications,
    # and no AI on JAP's key (launch decision D1; the AI is the user's own key or the
    # admin's grant, on any plan). The prices are for display only: the amount actually
    # charged is the one set on the plan at the payment provider, and the two have to be
    # kept in step by hand.
    jap_plus_boards: int = 10
    jap_plus_daily_applications: int = 10
    jap_plus_price_inr: int = 499
    jap_plus_price_usd: float = 9.0
    # How long a plan outlives the period it was paid for when a renewal fails. Never zero:
    # the provider is still retrying, and an account must not lose its plan on the day.
    jap_billing_grace_days: int = 3
    # Off until a provider is configured: no Upgrade in Settings and /api/billing/checkout
    # refuses. Nothing else about a plan changes, so an account that already has one keeps it.
    jap_billing_enabled: bool = False

    # Razorpay, which sells in India (IDEA-21). The key id and secret are the API login, the
    # webhook secret signs what Razorpay posts back, and the plan id is the plan Plus is
    # bought as — created once in their dashboard, since its price is theirs to hold.
    # Empty means a stand-in for the real API (tests set it); never a real key in .env.example.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    razorpay_plan_id_plus: str = ""
    razorpay_url: str = "https://api.razorpay.com"
    # The mailbox JAP sends from (E4): any SMTP server with a password, GoDaddy Professional
    # Email by default. A local test mailbox can take plain SMTP (SMTP_SSL=false) instead.
    smtp_host: str = "smtpout.secureserver.net"
    smtp_port: int = 465
    smtp_ssl: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
    # The shared secret an inbound-email service sends with a posted message (IDEA-16,
    # api/inbound.py), in the X-JAP-Inbound header. Empty: that route answers 404.
    jap_inbound_secret: str = ""
    # The same mailbox read over IMAP for a security code a job site emails (A5).
    imap_host: str = "imap.secureserver.net"
    imap_port: int = 993
    imap_ssl: bool = True
    # Older .env files name the Gmail login these; used when SMTP_USERNAME is empty.
    gmail_address: str = ""
    gmail_app_password: str = ""

    @property
    def email_from(self) -> str:
        return self.smtp_username or self.gmail_address

    @property
    def email_password(self) -> str:
        return self.smtp_password if self.smtp_username else self.gmail_app_password

    # Sign in with Google (S16). All three empty means the button is not shown and the
    # routes answer Not Found, the same shape as Turnstile, so a fresh clone and make test
    # need nothing from Google. The redirect URL must match the one on the OAuth client.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_url: str = ""
    # Only a stand-in for testing points this elsewhere (tests/test_google.py).
    google_issuer_url: str = "https://accounts.google.com"

    @property
    def google_ready(self) -> bool:
        return bool(
            self.google_client_id and self.google_client_secret and self.google_redirect_url
        )

    inngest_base_url: str = "http://inngest:8288"

    # How many searches may run at once across the whole service (T6). Each user is held to
    # one of their own by run-pipeline's keyed concurrency, so this is the ceiling on how
    # many different people search at the same time. Three is a starting point, not a
    # measurement: what a run shares is Postgres, Ollama's embedding and the OpenRouter
    # budget, and which of those gives first is to be watched before raising it.
    jap_max_parallel_runs: int = 3

    # How long a board's jobs count as fresh (T6). A board is shared, so when two people
    # who follow it search minutes apart the second search reuses the first one's fetch
    # instead of asking the ATS again. It never skips reading a job description that
    # somebody now wants: only the HTTP fetch is skipped (functions/fetch_board.py).
    jap_board_fresh_minutes: int = 10

    jap_tz: str = "Asia/Kolkata"

    # Resumes, screenshots, cover letters and backups (data/ on the Mac, data-live/ on the
    # server).
    jap_data_dir: str = "/data"
    # How long the files an application leaves behind are kept (IDEA-14, pipeline/retention.py).
    # A trace is megabytes and only useful while a recent failure is being looked into; a
    # screenshot is small and is the only evidence of what was sent, so it stays much longer.
    # 0 keeps them for ever. Resumes and cover letters have no retention: only deleting the
    # account removes those.
    jap_keep_traces_days: int = 30
    jap_keep_screenshots_days: int = 180
    # An age public key (age1...). Set it and every dump is encrypted to that recipient as
    # it is written, so the only copy that ever touches the disk is already unreadable
    # (IDEA-11). The server holds the **public** key alone: losing the whole machine, or
    # the hosting account it sits in, does not hand anyone the resumes and answers inside
    # the dump, because the private key is kept off the server. Empty means a plain
    # .sql.gz, which is what local development wants and what live must not run on.
    jap_backup_age_recipient: str = ""

    # Where the confirmation email links back to (the frozen JD in the side panel).
    jap_ui_url: str = "http://localhost:5173"

    # Apply (A1): at least this many seconds between two of one account's submissions.
    jap_apply_gap_seconds: int = 30
    # Pacing across every account (IDEA-17). A job site sees the service, not one user:
    # this is the gap between any two applications to the same ATS, and the ceiling on
    # what the whole service sends in a day under each account's own cap. 0 turns either
    # off. Raise the ceiling as more workers are added, not before.
    jap_apply_ats_gap_seconds: int = 60
    jap_daily_applications_total: int = 200
    # How many application browsers may be open across the service at once (IDEA-15).
    # Each holds a worker and a few hundred MB, so raise it with the worker count, and
    # measure first: `make capacity` says how long one application really holds a browser.
    jap_apply_browsers: int = 4
    # An outbound proxy for the application browser (IDEA-18), as Playwright takes it:
    # http://user:pass@host:port. Empty means the server's own address, which is what
    # every application leaves from today. Watch the per-site rates on Settings before
    # turning this on; it costs money and some sites block known proxy ranges.
    jap_apply_proxy: str = ""

    # Sign-in (PRD S1 to S5). Secure cookies need HTTPS: true on dev, test and prod hosts.
    jap_cookie_secure: bool = False
    # When set, only this address can create the first (admin) account.
    jap_admin_email: str = ""
    jap_session_idle_days: int = 7
    jap_session_max_days: int = 30

    # Sign-up (PRD S1, S10). Tries allowed in an hour from one address and for one email, on
    # sign-up, forgot password and "send the link again" (auth/limits.py).
    jap_auth_tries_per_hour_ip: int = 20
    jap_auth_tries_per_hour_email: int = 5
    # The header a trusted proxy puts the visitor's address in (CF-Connecting-IP behind the
    # Cloudflare Tunnel). Empty: the connection's own address. Never set it without a proxy
    # that overwrites the header, or anyone can claim any address.
    jap_client_ip_header: str = ""
    # Cloudflare Turnstile on sign-up and forgot password. Both empty: no check (local).
    turnstile_site_key: str = ""
    turnstile_secret_key: str = ""

    # Logging (PRD O1 to O4). LOG_JSON=false prints plain lines, easier to read by eye in dev.
    log_level: str = "INFO"
    log_json: bool = True
    # Keep warnings, errors and timeline steps in the log_event table (O3, O8).
    jap_log_db: bool = True

    # Health alerts by email (O6). Off where nothing should be sent (dev and test).
    jap_alerts_enabled: bool = True
    jap_alert_errors_per_hour: int = 10

    # Sentry's free plan (O15): empty means off. Only redacted errors are sent.
    sentry_dsn: str = ""

    # The UI dev server is the only allowed browser origin.
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
