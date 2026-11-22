"""Read a security code the job site emailed (A5, IDEA-16).

Greenhouse sometimes asks for an 8-character code it emails before it takes an application.
A mail filter forwards those emails to JAP's mailbox (E4), and this module reads them over
IMAP: only emails from the job site that arrived after Submit was pressed, polled for up
to 3 minutes.

Matching on the sender and the time alone is only safe while one application runs at a
time. With several users applying side by side (IDEA-15) the newest Greenhouse email need
not be the one this person is waiting for, so a code is matched by **who it was sent to**
as well: each account has its own forwarding address, jap+<code_token>@..., and only an
email addressed to that account's token counts. An account with no token still matches on
sender alone, which is the old behaviour and is right only for a single-user stack.

Three ways a code arrives, and the form takes whichever comes first (waiter):

- the mailbox, matched by recipient (above);
- an inbound-email service that posts the mail to JAP and names the recipient, for
  providers whose forward drops the original To (api/inbound.py);
- the person reading the code and typing it in, which always works and is the only route
  on a stack with no mailbox (application.security_code).

The code is never logged or sent to a model. It is held on the application row only while
the form waits for it, and cleared the moment the form takes it.

`python -m pipeline.apply.security_code` (make code-mail-check) logs in once and says how
many emails from Greenhouse the inbox holds, to check the forwarding.
"""

import asyncio
import email
import imaplib
import re
import secrets
import sys
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.message import Message
from email.utils import parsedate_to_datetime

from config import get_settings

WAIT_S = 180
POLL_S = 10
# The mail server's clock and ours may differ a little.
SKEW = timedelta(seconds=60)
SENDERS = {"greenhouse": re.compile(r"greenhouse", re.I)}
# Where the address a forward was originally sent to survives, best first. Which of these
# a provider keeps differs, so all of them are read rather than one being assumed.
TO_HEADERS = ("Delivered-To", "X-Forwarded-To", "X-Original-To", "Envelope-To", "To", "Cc")

GetCode = Callable[[datetime, int], Awaitable[str | None]]


class MailboxError(Exception):
    """The mailbox could not be reached or logged in to."""


def _text(msg: Message) -> str:
    parts: list[str] = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if part.get_content_type() == "text/html":
            text = re.sub(r"<[^>]+>", " ", text)
        parts.append(text)
    return " ".join(" ".join(parts).split())


def code_in(text: str, length: int) -> str | None:
    """The code in an email's text: the first token of exactly `length` letters and digits
    after the word "code" with a digit or mixed case, else the one all-capitals token."""
    at = re.search(r"\bcode\b", text, re.I)
    if at is None:
        return None
    tokens = re.findall(rf"\b[A-Za-z0-9]{{{length}}}\b", text[at.end() :])
    for token in tokens:
        if any(c.isdigit() for c in token) or (token != token.lower() and token != token.upper()):
            return str(token)
    # All capitals with no digit: taken only when it is the one such token in the email.
    caps = [t for t in tokens if t.isupper()]
    return str(caps[0]) if len(caps) == 1 else None


def _from_site(msg: Message, site: str) -> bool:
    pattern = SENDERS.get(site, re.compile(re.escape(site), re.I))
    # A Gmail filter forward keeps the original sender; the subject and body also name it.
    head = " ".join(str(msg.get(h, "")) for h in ("From", "Reply-To", "Subject", "Sender"))
    return bool(pattern.search(head))


def new_token() -> str:
    """A token for an account's forwarding address. Short enough to type into a mail
    filter, random enough that nobody can guess another account's."""
    return secrets.token_hex(8)


def address_for(token: str) -> str | None:
    """The forwarding address an account's mail filter should send codes to, or None on a
    stack with no mailbox. The token still stands: an inbound-email service delivers to
    its own domain (api/inbound.py) and only the token has to match."""
    name, _, domain = get_settings().email_from.partition("@")
    return f"{name}+{token}@{domain}" if domain else None


def _for_token(msg: Message, token: str | None) -> bool:
    """Whether this email was addressed to this account's token. With no token, every
    email passes, which is the single-user behaviour."""
    if not token:
        return True
    where = " ".join(str(msg.get(h, "")) for h in TO_HEADERS).lower()
    return f"+{token.lower()}@" in where


def _arrived(msg: Message) -> datetime | None:
    try:
        when = parsedate_to_datetime(str(msg.get("Date", "")))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _connect() -> imaplib.IMAP4:
    s = get_settings()
    if not s.email_from or not s.email_password:
        raise MailboxError("No mailbox in .env (SMTP_USERNAME and SMTP_PASSWORD)")
    try:
        box: imaplib.IMAP4 = (
            imaplib.IMAP4_SSL(s.imap_host, s.imap_port, timeout=20)
            if s.imap_ssl
            else imaplib.IMAP4(s.imap_host, s.imap_port, timeout=20)
        )
        box.login(s.email_from, s.email_password)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailboxError(f"Could not log in to {s.imap_host}: {exc}") from exc
    return box


def fetch_code(site: str, since: datetime, length: int, token: str | None = None) -> str | None:
    """One look at the inbox: the code in the newest email from the site that arrived
    after `since` and was addressed to this account's token, or None."""
    box = _connect()
    try:
        box.select("INBOX", readonly=True)
        day = (since - SKEW).strftime("%d-%b-%Y")
        _typ, data = box.search(None, "SINCE", day)
        ids = (data[0] or b"").split() if data else []
        for num in reversed(ids[-50:]):  # newest first
            _typ, parts = box.fetch(num.decode(), "(RFC822)")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None)
            if not isinstance(raw, bytes):
                continue
            msg = email.message_from_bytes(raw)
            when = _arrived(msg)
            if when is None or when < since - SKEW or not _from_site(msg, site):
                continue
            if not _for_token(msg, token):
                continue  # somebody else's code, waiting at the same moment
            code = code_in(_text(msg), length)
            if code:
                return code
        return None
    finally:
        try:
            box.logout()
        except (OSError, imaplib.IMAP4.error):
            pass


async def typed_code(app_id: str) -> str | None:
    """A code the person typed in, or one an inbound-email service posted (api/inbound.py).
    Read in a session of its own: the submitting session committed before the browser
    opened, and this has to see a write another process made since."""
    from sqlalchemy import select

    from database.models import Application
    from database.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        return await session.scalar(
            select(Application.security_code).where(Application.id == uuid.UUID(app_id))
        )


async def mark_wanted(app_id: str) -> None:
    """Say the site is asking, so the screen can offer the box to type it in."""
    from sqlalchemy import update

    from database.models import Application
    from database.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        await session.execute(
            update(Application)
            .where(Application.id == uuid.UUID(app_id))
            .values(code_wanted_at=datetime.now(UTC))
        )
        await session.commit()


def waiter(
    site: str,
    app_id: str,
    token: str | None = None,
    reads_mail: bool = True,
    wait_s: int = WAIT_S,
    poll_s: int = POLL_S,
) -> GetCode:
    """A get_code for submit_form: whichever of the three routes answers first, or None
    once wait_s has passed. The wait is bounded because the browser is held open for all
    of it, and a held browser is a worker that cannot start anybody else's application
    (IDEA-15). Someone who misses the window is told to apply from the posting."""

    async def get_code(since: datetime, length: int) -> str | None:
        await mark_wanted(app_id)
        deadline = asyncio.get_running_loop().time() + wait_s
        while True:
            typed = await typed_code(app_id)
            if typed:
                return typed
            if reads_mail:
                code = await asyncio.to_thread(fetch_code, site, since, length, token)
                if code:
                    return code
            if asyncio.get_running_loop().time() + poll_s > deadline:
                return None
            await asyncio.sleep(poll_s)

    return get_code


def site_of(url: str) -> str | None:
    """The job site whose code emails JAP can read, from the form's URL."""
    return "greenhouse" if "greenhouse.io" in url.lower() else None


def check() -> int:
    s = get_settings()
    box = _connect()
    try:
        box.select("INBOX", readonly=True)
        since = (datetime.now(UTC) - timedelta(days=30)).strftime("%d-%b-%Y")
        _typ, data = box.search(None, "SINCE", since, "FROM", "greenhouse")
        count = len((data[0] or b"").split()) if data else 0
    finally:
        box.logout()
    print(f"Logged in to {s.imap_host} as {s.email_from}.")
    print(f"Emails from Greenhouse in the last 30 days: {count}.")
    if count == 0:
        print("None yet. Check the Gmail filter forwards Greenhouse security code emails here.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(check())
    except MailboxError as exc:
        print(exc)
        sys.exit(1)
