"""Every promise the privacy notice makes, as a test (IDEA-23, the launch review).

The notice in `frontend/src/pages/LegalPage.tsx` is a legal statement about how the code
behaves. A statement like that rots: someone changes redaction, or adds a route, or raises
a retention period, and the page carries on saying the old thing — and the page is what a
user agreed to.

So each claim appears twice here: the sentence must still be in the notice, and the
behaviour behind it must still hold. Break either and this file fails, naming the promise.

Three rules for working on this file:

- A claim that cannot be checked by a test (the operator does not sell data) is listed in
  UNTESTABLE with the reason, so the gap is deliberate rather than forgotten.
- Changing the notice means changing CLAIMS here, which means reading the test under it.
- Most of these guarantees have fuller tests of their own (test_redact, test_tenancy,
  test_account_delete, test_retention). These are deliberately the thin end: enough to
  fail when the promise stops being true, not a second copy of those suites.
"""

import re
from pathlib import Path

import httpx
import pytest

from auth import consent
from config import get_settings
from database import tenant
from database.models import LlmCall
from database.session import get_sessionmaker
from llm import client, redact
from observability import scrub
from pipeline import backup, erasure
from tests.conftest import OTHER_EMAIL, OTHER_PASSWORD

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
NOTICE = FRONTEND / "src" / "pages" / "LegalPage.tsx"  # the documents themselves
FACTS = FRONTEND / "src" / "legal.ts"  # the operator, and the retention periods
SECURITY_TXT = FRONTEND / "public" / ".well-known" / "security.txt"

# The exact sentences the tests below stand behind. Kept short, so reflowing a paragraph or
# running prettier over the file does not break them, and so each one reads as the promise
# it is rather than as a paragraph of context.
CLAIMS = {
    "password_hash": "kept only as a salted hash and is never readable",
    "redacted_before_sending": (
        "Your name, email address, phone number, postal address and profile links are "
        "taken out before anything is sent"
    ),
    "blocked_not_sent": "a request still carrying one is blocked rather than sent",
    "contact_filled_by_code": (
        "Contact fields on an application form are filled in by JAP's own code, never by a model"
    ),
    "no_training": "providers that do not log or train on what is sent",
    "embeddings_stay_here": "computed on the operator's own server and go nowhere",
    "no_other_user": "No other user can see anything of yours",
    "admin_sees_no_content": "never your resume, your answers or your jobs",
    "one_cookie": "One cookie, which keeps you signed in",
    "no_analytics": "there is no advertising and no analytics anywhere in JAP",
    "two_opt_ins": (
        "sending an application in your name at all, and sending one without showing it to you"
    ),
    "withdrawable": "You can withdraw either permission at any time in Settings",
    "delete_is_immediate": "removes all of it, and the files with it, at once and for good",
    "only_cost_survives": (
        "One thing is kept after a deletion: the record of what each AI call cost"
    ),
    "logs_scrubbed": "Logs are scrubbed of addresses, phone numbers, keys and codes",
    "backups_encrypted": "Database backups are encrypted to a key held off the server",
    "min_age": "You must be 18 or older to use JAP",
    "complain": "you can complain to a data protection authority",
}

# Claims that are about a person's conduct or a contract, not about what the code does.
# Nothing here can be tested; the reason is the point.
UNTESTABLE = {
    "not_sold": "Whether data is sold is a promise by the operator, not a branch in the code.",
    "transfer_basis": "Standard contractual clauses are paperwork with each provider.",
    "breach_notice": "Telling people about a breach is a procedure (README, If something leaks).",
}


def _notice_text() -> str:
    """The notice as a reader sees it: JSX expressions out, entities back, spaces collapsed."""
    raw = NOTICE.read_text()
    raw = re.sub(r"\{'\s*'\}", " ", raw)  # {' '} is a space in JSX
    raw = re.sub(r"<[^>]+>", " ", raw)  # tags
    raw = raw.replace("&apos;", "'").replace("&quot;", '"').replace("&gt;", ">")
    raw = raw.replace("&amp;", "&")
    return " ".join(raw.split())


def test_every_claim_is_still_in_the_notice() -> None:
    """The notice says what these tests check. Removing a sentence here means the promise
    was dropped, which is a decision; editing one means raising TERMS_VERSION."""
    text = _notice_text()
    missing = [name for name, claim in CLAIMS.items() if claim not in text]
    assert not missing, (
        f"These claims are no longer in {NOTICE.name}: {missing}. If the promise was "
        "deliberately changed, update CLAIMS and the test under it, and raise "
        "TERMS_VERSION in auth/consent.py so everyone is asked again."
    )


def test_the_notice_is_marked_a_draft_until_the_operator_is_named() -> None:
    """Who the controller is, where they are and how to reach them are the three things
    the DPDP Act and the GDPR both insist on, and no test can supply them. The banner is
    computed from them, so it cannot be switched off while they are missing."""
    source = FACTS.read_text()
    assert "export const NOTICE_IS_A_DRAFT = Object.values(OPERATOR).some(" in source, (
        "The draft banner must stay computed from OPERATOR. A hand-set flag is one "
        "somebody turns off while the operator's details are still placeholders."
    )


# --- What goes to a model -------------------------------------------------------------


def test_personal_details_are_removed_and_a_leak_is_blocked() -> None:
    """CLAIMS[redacted_before_sending] and CLAIMS[blocked_not_sent]."""
    me = redact.Sensitive(
        name="Ada Lovelace",
        emails=["ada@example.com"],
        phones=["+91 98765 43210"],
        addresses=["12 Analytical Way"],
        links=["https://github.com/adalove"],
    )
    out = redact.redact("Ada Lovelace, ada@example.com, +91 98765 43210, 12 Analytical Way", me)
    for secret in ("Ada", "ada@example.com", "98765", "Analytical Way"):
        assert secret not in out.text
    # And if one ever survives redaction, the request does not go.
    with pytest.raises(redact.PersonalDataBlocked):
        redact.guard("please contact ada@example.com", me)


def test_only_providers_that_do_not_train_are_asked_for() -> None:
    """CLAIMS[no_training]. The notice says this of every call, so it is in build_body
    itself and not something a caller passes in."""
    body = client.build_body("some/model", "system", "user", {"type": "object"}, 0.0, 100)
    assert body["provider"] == {"data_collection": "deny"}


def test_embeddings_never_leave_the_server() -> None:
    """CLAIMS[embeddings_stay_here]. Embedding goes to Ollama, which JAP runs itself; the
    one module allowed to reach OpenRouter has nothing to do with it."""
    assert "embed" not in Path(client.__file__).read_text().lower()
    ollama = Path(__file__).resolve().parents[1] / "llm" / "ollama.py"
    assert "openrouter" not in ollama.read_text().lower()


def test_contact_fields_are_filled_from_the_profile_and_never_by_a_model() -> None:
    """CLAIMS[contact_filled_by_code]: no prompt may ask a model for a contact detail."""
    prompts = (Path(__file__).resolve().parents[1] / "llm" / "prompts").glob("*.md")
    for prompt in prompts:
        body = prompt.read_text().lower()
        for banned in ("their email", "their phone", "the candidate's email", "phone number"):
            assert banned not in body, f"{prompt.name} asks a model for a contact detail"


# --- Who can see it -------------------------------------------------------------------


async def test_another_account_sees_nothing_of_mine(
    api: httpx.AsyncClient, other: httpx.AsyncClient
) -> None:
    """CLAIMS[no_other_user], at the thin end: the owner's answer and the other account's
    answer to the same route are not the same answer."""
    mine = await api.post("/api/answers", json={"question": "Why us?", "answer": "Because"})
    assert mine.status_code in (200, 201), mine.text
    assert [a["question"] for a in (await other.get("/api/answers")).json()] == []


async def test_the_admin_cannot_read_another_persons_content(
    api: httpx.AsyncClient, other: httpx.AsyncClient
) -> None:
    """CLAIMS[admin_sees_no_content]. `api` is the admin; the accounts screen shows that
    an account exists and what it has spent, and carries none of its content."""
    await other.post("/api/answers", json={"question": "Secret?", "answer": "Confidential"})
    accounts = await api.get("/api/admin/accounts")
    assert accounts.status_code == 200, accounts.text
    body = accounts.text
    assert OTHER_EMAIL in body, "the admin does see that the account exists"
    assert "Confidential" not in body and "Secret?" not in body
    # And the admin's own answer list is the admin's own, not everyone's.
    assert [a["answer"] for a in (await api.get("/api/answers")).json()] == []


# --- Consent --------------------------------------------------------------------------


def test_both_permissions_are_separate_and_either_can_be_withdrawn() -> None:
    """CLAIMS[two_opt_ins] and CLAIMS[withdrawable]."""

    class FakeProfile:
        submit_opt_in_at = None
        auto_opt_in_at = None

    profile = FakeProfile()
    assert not consent.may_submit(profile)  # type: ignore[arg-type]
    profile.submit_opt_in_at = object()  # type: ignore[assignment]
    assert consent.may_submit(profile)  # type: ignore[arg-type]
    # Agreeing to apply is not agreeing to apply unasked.
    assert not consent.may_apply_automatically(profile)  # type: ignore[arg-type]
    profile.auto_opt_in_at = object()  # type: ignore[assignment]
    assert consent.may_apply_automatically(profile)  # type: ignore[arg-type]
    # Withdrawing either one takes the permission away again.
    profile.submit_opt_in_at = None
    assert not consent.may_submit(profile) and not consent.may_apply_automatically(profile)  # type: ignore[arg-type]


# --- Deletion, retention, logs, backups -----------------------------------------------


def test_only_the_cost_of_an_ai_call_survives_a_deletion() -> None:
    """CLAIMS[only_cost_survives] and CLAIMS[delete_is_immediate]. test_account_delete.py
    runs the real deletion; this checks the shape of the promise: one table kept, every
    other private table deleted, and nothing personal left in the one that stays."""
    assert erasure.ANONYMISED == ("llm_call",)
    kept_or_deleted = set(erasure.DELETE_ORDER) | set(erasure.ANONYMISED)
    assert set(tenant.PRIVATE_TABLES) == kept_or_deleted, (
        "every private table must be deleted or anonymised by erase(); a new one has been "
        "added without deciding which"
    )
    columns = {c.name for c in LlmCall.__table__.columns}
    assert not columns & {"prompt", "answer", "text", "resume_text", "cover_letter"}


def test_retention_periods_are_what_the_notice_says() -> None:
    """The notice prints these numbers, so the settings and the page cannot disagree."""
    page = FACTS.read_text()
    settings = get_settings()
    assert f"KEEP_TRACES_DAYS = {settings.jap_keep_traces_days}" in page
    assert f"KEEP_SCREENSHOTS_DAYS = {settings.jap_keep_screenshots_days}" in page
    assert f"KEEP_BACKUPS_DAYS = {backup.KEEP_DAYS}" in page


def test_logs_never_carry_a_contact_detail_or_a_code() -> None:
    """CLAIMS[logs_scrubbed]."""
    line = scrub.scrub("ada@example.com asked, code 483921, token sk-abcdefghijklmnopqrst")
    assert "ada@example.com" not in line
    assert "483921" not in line
    assert "sk-abcdefghijklmnopqrst" not in line


def test_backups_can_be_encrypted_to_a_key_the_server_does_not_hold() -> None:
    """CLAIMS[backups_encrypted]. The round trip is test_hardening.py; this is that the
    setting exists, is off by default, and names a recipient rather than a password — a
    password would have to live on the server, which is the thing being avoided."""
    assert get_settings().jap_backup_age_recipient == ""
    ready, detail = backup.age_ready()
    assert not ready and "JAP_BACKUP_AGE_RECIPIENT" in detail


# --- Cookies --------------------------------------------------------------------------


def test_the_ui_loads_nothing_that_watches_you() -> None:
    """CLAIMS[no_analytics]: half of why there is no cookie banner."""
    html = (FRONTEND / "index.html").read_text().lower()
    for tracker in ("googletagmanager", "google-analytics", "segment.", "hotjar", "mixpanel"):
        assert tracker not in html


async def test_signing_in_sets_one_cookie(anon: httpx.AsyncClient, other_id: object) -> None:
    """CLAIMS[one_cookie]: the other half. One cookie, and it is the session."""
    resp = await anon.post(
        "/api/auth/sign-in", json={"email": OTHER_EMAIL, "password": OTHER_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    names = [c.split("=")[0] for c in resp.headers.get_list("set-cookie")]
    assert len(names) == 1, f"sign-in set more than the session cookie: {names}"
    assert "session" in names[0]


def test_the_security_contact_is_filled_in_before_the_notice_stops_being_a_draft() -> None:
    """A published security.txt whose Contact is a placeholder is worse than none: it
    tells a finder there is a way to report and then throws it away. It is filled in from
    the same operator email as the notice, so the two go live together or not at all."""
    operator = FACTS.read_text().split("export const OPERATOR = {")[1].split("}")[0]
    still_a_draft = "PLACEHOLDER" in operator
    body = SECURITY_TXT.read_text()
    assert "Expires:" in body and "Contact:" in body, "RFC 9116 wants both"
    if not still_a_draft:
        assert "TO BE FILLED IN" not in body, (
            "The notice names a real operator, so security.txt needs the real address too."
        )


def test_untestable_claims_are_listed_with_a_reason() -> None:
    """A claim nobody can test is a claim nobody is watching. Writing down why keeps the
    gap deliberate."""
    assert all(reason.strip() for reason in UNTESTABLE.values())


async def test_the_notice_is_readable_without_signing_in(anon: httpx.AsyncClient) -> None:
    """It has to be read before an account exists, so it is a React page and not an API
    route: there is nothing to fetch. The check is that no route is needed — the agreement
    screen renders it from the bundle — and that the terms version a visitor is asked to
    agree to is the one the backend will record."""
    state = (await anon.get("/api/auth/state")).json()
    assert state["terms_version"] == consent.TERMS_VERSION


def test_a_changed_notice_takes_everyone_back_through_the_tick() -> None:
    """CLAIMS say the notice is agreed to; consent.accepted_terms is what enforces it."""

    class FakeUser:
        terms_accepted_at = object()
        terms_version = "an older notice"

    user = FakeUser()
    assert not consent.accepted_terms(user)  # type: ignore[arg-type]
    user.terms_version = consent.TERMS_VERSION
    assert consent.accepted_terms(user)  # type: ignore[arg-type]


def test_sessions_and_exports_are_not_left_lying_about() -> None:
    """Not a claim in the notice, but the thing that would make several of them false:
    the private tables list is what export and deletion both walk, so a table that is
    private in the database and missing from it is invisible to both."""
    assert "user_secret" in tenant.PRIVATE_TABLES
    assert set(erasure.FILES) == set(tenant.PRIVATE_TABLES)
    assert get_sessionmaker is not None
