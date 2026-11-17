"""Settings read from .env (see .env.example for what each one does)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://jap:jap@db:5432/jap"

    # Which environment this stack is (H3): dev, test or prod. The UI shows a DEV or TEST badge.
    jap_env: str = "dev"
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
    # reading shared job postings, and the limit on everything together.
    jap_monthly_cost_cap_usd: float = 1.0
    jap_system_cost_cap_usd: float = 5.0
    jap_total_cost_cap_usd: float = 20.0
    # The free plan (pipeline/limits.py): companies one account may follow, and applications
    # it may send a day. The admin is not held to either, and can set any one account's own
    # numbers in Settings > Accounts. The AI allowance above is sized to go with them: an
    # application costs about 2 to 3 cents of AI, so one a day is under $1 a month.
    jap_free_boards: int = 1
    jap_free_daily_applications: int = 1

    # The mailbox JAP sends from (E4): any SMTP server with a password, GoDaddy Professional
    # Email by default. A local test mailbox can take plain SMTP (SMTP_SSL=false) instead.
    smtp_host: str = "smtpout.secureserver.net"
    smtp_port: int = 465
    smtp_ssl: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
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

    inngest_base_url: str = "http://inngest:8288"

    jap_tz: str = "Asia/Kolkata"

    # Resumes, screenshots, cover letters and backups (data/ on the Mac).
    jap_data_dir: str = "/data"

    # Where the confirmation email links back to (the frozen JD in the side panel).
    jap_ui_url: str = "http://localhost:5173"

    # Apply (A1): at least this many seconds between two submissions.
    jap_apply_gap_seconds: int = 30

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
