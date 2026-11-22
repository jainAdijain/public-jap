"""A5: the security code Greenhouse emails, read from JAP's mailbox over IMAP."""

import imaplib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any

import pytest

from config import get_settings
from pipeline.apply import security_code as sc

PRESSED = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)
BODY = (
    "Hi Asha, copy and paste this code into the security code field on your application: "
    "Ab3dE5fG. This code expires in 10 minutes."
)


def _mail(sender: str, at: datetime, body: str, subject: str = "Security code") -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "jap-admin@example.com"
    msg["Subject"] = subject
    msg["Date"] = format_datetime(at)
    msg.set_content(body)
    return msg.as_bytes()


class FakeImap:
    mails: list[bytes] = []
    logins: list[tuple[str, str]] = []

    def __init__(self, host: str, port: int, timeout: int = 0) -> None:
        self.host = host

    def login(self, user: str, password: str) -> None:
        FakeImap.logins.append((user, password))

    def select(self, box: str, readonly: bool = False) -> None:
        pass

    def search(self, charset: Any, *criteria: str) -> tuple[str, list[bytes]]:
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(FakeImap.mails)))]

    def fetch(self, num: bytes, what: str) -> tuple[str, list[Any]]:
        return "OK", [(b"1 (RFC822)", FakeImap.mails[int(num) - 1]), b")"]

    def logout(self) -> None:
        pass


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeImap]]:
    monkeypatch.setenv("SMTP_USERNAME", "jap-admin@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    get_settings.cache_clear()
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeImap)
    FakeImap.mails, FakeImap.logins = [], []
    yield FakeImap
    get_settings.cache_clear()


def test_the_code_is_found_in_the_email_text() -> None:
    assert sc.code_in(BODY, 8) == "Ab3dE5fG"
    assert sc.code_in("Your security code is 4F7K2Q9X", 8) == "4F7K2Q9X"
    assert sc.code_in("Your security code is: QWERTYUI", 8) == "QWERTYUI"
    # Plain words after "code" are not codes.
    assert sc.code_in("The security code expires tomorrow for everyone", 8) is None
    assert sc.code_in("No code here: 12345", 8) is None


def test_only_greenhouse_emails_after_submit_count(mailbox: type[FakeImap]) -> None:
    mailbox.mails = [
        _mail("no-reply@us.greenhouse-mail.io", PRESSED - timedelta(minutes=10),
              BODY.replace("Ab3dE5fG", "0ld0ld00")),
        _mail("Someone <someone@example.com>", PRESSED + timedelta(seconds=20),
              BODY.replace("Ab3dE5fG", "N0tTh1s0")),
        _mail("Greenhouse <no-reply@us.greenhouse-mail.io>", PRESSED + timedelta(seconds=30),
              BODY),
    ]  # fmt: skip
    assert sc.fetch_code("greenhouse", PRESSED, 8) == "Ab3dE5fG"
    assert mailbox.logins == [("jap-admin@example.com", "secret")]


def test_an_old_code_is_never_used(mailbox: type[FakeImap]) -> None:
    mailbox.mails = [_mail("no-reply@greenhouse.io", PRESSED - timedelta(minutes=5), BODY)]
    assert sc.fetch_code("greenhouse", PRESSED, 8) is None


APP = "00000000-0000-0000-0000-0000000000a1"


@pytest.fixture
def no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """These exercise the mailbox half of the waiter. The other two routes it watches read
    and write the application row, which is tested against a real one in test_apply.py."""

    async def nothing(app_id: str) -> None:
        return None

    monkeypatch.setattr(sc, "mark_wanted", nothing)
    monkeypatch.setattr(sc, "typed_code", nothing)


async def test_the_waiter_polls_until_the_code_arrives(
    mailbox: type[FakeImap], no_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    looks: list[int] = []
    real = sc.fetch_code

    def fetch(site: str, since: datetime, length: int, token: str | None = None) -> str | None:
        looks.append(1)
        if len(looks) == 2:
            mailbox.mails.append(
                _mail("no-reply@greenhouse.io", PRESSED + timedelta(seconds=5), BODY)
            )
        return real(site, since, length, token)

    monkeypatch.setattr(sc, "fetch_code", fetch)
    code = await sc.waiter("greenhouse", APP, wait_s=1, poll_s=0)(PRESSED, 8)
    assert code == "Ab3dE5fG" and len(looks) == 2


async def test_the_waiter_gives_up(mailbox: type[FakeImap], no_db: None) -> None:
    assert await sc.waiter("greenhouse", APP, wait_s=0, poll_s=1)(PRESSED, 8) is None


# IDEA-16: whose code is it? ---------------------------------------------------------------


def _to(sender: str, at: datetime, body: str, to: str, header: str = "To") -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "jap@example.com"
    if header != "To":
        msg[header] = to
    else:
        msg.replace_header("To", to)
    msg["Subject"] = "Security code"
    msg["Date"] = format_datetime(at)
    msg.set_content(body)
    return msg.as_bytes()


def test_a_code_is_matched_to_the_account_it_was_sent_to(mailbox: type[FakeImap]) -> None:
    """Two people waiting at the same moment: each gets their own code, and neither gets
    the other's. Without this, the newest Greenhouse email wins and that is a lottery."""
    mine, yours = "aaaa1111", "bbbb2222"
    mailbox.mails = [
        _to("no-reply@greenhouse.io", PRESSED + timedelta(seconds=10),
            BODY.replace("Ab3dE5fG", "MINE1234"), f"jap+{mine}@example.com"),
        _to("no-reply@greenhouse.io", PRESSED + timedelta(seconds=20),
            BODY.replace("Ab3dE5fG", "YOURS567"), f"jap+{yours}@example.com"),
    ]  # fmt: skip
    assert sc.fetch_code("greenhouse", PRESSED, 8, mine) == "MINE1234"
    assert sc.fetch_code("greenhouse", PRESSED, 8, yours) == "YOURS567"
    # An account whose token is on no email gets nothing, rather than someone else's.
    assert sc.fetch_code("greenhouse", PRESSED, 8, "cccc3333") is None
    # No token at all is the single-user case: the newest code, whoever it was for.
    assert sc.fetch_code("greenhouse", PRESSED, 8) == "YOURS567"


@pytest.mark.parametrize("header", ["Delivered-To", "X-Forwarded-To", "X-Original-To", "Cc"])
def test_the_recipient_is_found_wherever_the_forward_put_it(
    mailbox: type[FakeImap], header: str
) -> None:
    """Which header survives a forward differs by provider, so all of them are read."""
    mailbox.mails = [
        _to("no-reply@greenhouse.io", PRESSED + timedelta(seconds=10), BODY,
            "jap+dddd4444@example.com", header)
    ]  # fmt: skip
    assert sc.fetch_code("greenhouse", PRESSED, 8, "dddd4444") == "Ab3dE5fG"


def test_an_address_is_built_from_the_mailbox_login(mailbox: type[FakeImap]) -> None:
    assert sc.address_for("abc123") == "jap-admin+abc123@example.com"
    assert len(sc.new_token()) == 16 and sc.new_token() != sc.new_token()


def test_no_mailbox_means_no_address(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SMTP_USERNAME", "SMTP_PASSWORD", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
        monkeypatch.setenv(name, "")
    get_settings.cache_clear()
    assert sc.address_for("abc123") is None
    get_settings.cache_clear()


def test_no_mailbox_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SMTP_USERNAME", "SMTP_PASSWORD", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
        monkeypatch.setenv(name, "")
    get_settings.cache_clear()
    with pytest.raises(sc.MailboxError):
        sc.fetch_code("greenhouse", PRESSED, 8)
    get_settings.cache_clear()


def test_only_greenhouse_forms_read_mail() -> None:
    assert sc.site_of("https://job-boards.greenhouse.io/anthropic/jobs/1") == "greenhouse"
    assert sc.site_of("https://jobs.ashbyhq.com/linear/1/application") is None
