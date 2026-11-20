"""Traces and screenshots are deleted after their time; the user's own documents are not
(IDEA-14, pipeline/retention.py)."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from pipeline import retention, storage


def _file(path: Path, days_old: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    when = (datetime.now(UTC) - timedelta(days=days_old)).timestamp()
    os.utime(path, (when, when))
    return path


def _ages(owner: uuid.UUID) -> dict[str, Path]:
    return {
        "old_trace": _file(storage.user_path(owner, "traces", "old.zip"), 40),
        "new_trace": _file(storage.user_path(owner, "traces", "new.zip"), 2),
        "old_shot": _file(storage.user_path(owner, "screenshots", "old.png"), 400),
        "new_shot": _file(storage.user_path(owner, "screenshots", "new.png"), 40),
        "resume": _file(storage.user_path(owner, "resumes", "ancient.pdf"), 900),
        "letter": _file(storage.user_path(owner, "letters", "ancient.pdf"), 900),
    }


def test_old_traces_go_and_recent_ones_and_documents_stay(db: str, owner: uuid.UUID) -> None:
    files = _ages(owner)
    assert retention.sweep() == {"traces": 1, "screenshots": 1, "form_checks": 0}
    assert not files["old_trace"].exists() and not files["old_shot"].exists()
    assert files["new_trace"].exists() and files["new_shot"].exists()
    # A resume and a cover letter are the user's own: no age ever removes them.
    assert files["resume"].exists() and files["letter"].exists()


def test_every_users_folder_is_swept_not_only_the_first(db: str, owner: uuid.UUID) -> None:
    theirs = _file(storage.user_path(uuid.uuid4(), "traces", "old.zip"), 40)
    mine = _file(storage.user_path(owner, "traces", "old.zip"), 40)
    assert retention.sweep()["traces"] == 2
    assert not theirs.exists() and not mine.exists()


def test_a_retention_of_zero_keeps_everything(
    db: str, owner: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import get_settings

    monkeypatch.setenv("JAP_KEEP_TRACES_DAYS", "0")
    monkeypatch.setenv("JAP_KEEP_SCREENSHOTS_DAYS", "0")
    get_settings.cache_clear()
    files = _ages(owner)
    assert retention.sweep() == {"traces": 0, "screenshots": 0, "form_checks": 0}
    assert all(f.exists() for f in files.values())
    get_settings.cache_clear()


def test_the_form_tests_own_screenshots_are_swept_too(db: str, owner: uuid.UUID) -> None:
    old = _file(storage.root() / "form_checks" / "greenhouse-20250101.png", 400)
    new = _file(storage.root() / "form_checks" / "lever-20260101.png", 3)
    assert retention.sweep()["form_checks"] == 1
    assert not old.exists() and new.exists()


async def test_an_applied_application_keeps_its_record_and_loses_its_trace(
    db: str, owner: uuid.UUID, sql: psycopg.Connection[Any], tmp_path: Path
) -> None:
    """An Applied application is frozen (PRD A2): the database refuses every change to one
    but email_sent_at. Those are exactly the rows with a trace, so the sweep deletes the
    file and leaves the row alone — and the job says "no trace" because the screens ask the
    filesystem, not the column."""
    from tests.test_apply import _listing, _resume

    trace = _file(storage.user_path(owner, "traces", "gone.zip"), 40)
    shot = _file(storage.user_path(owner, "screenshots", "kept.png"), 2)
    sql.execute(
        "INSERT INTO application (listing_id, posting_id, mode, resume_version_id, status,"
        " trace_path, screenshot_path) SELECT l.id, l.primary_posting_id, 'review', %s,"
        " 'applied', %s, %s FROM listing l WHERE l.id = %s",
        (_resume(sql, tmp_path), str(trace), str(shot), _listing(sql)),
    )
    assert retention.sweep() == {"traces": 1, "screenshots": 0, "form_checks": 0}
    assert not trace.exists() and shot.exists()
    row = sql.execute("SELECT trace_path, screenshot_path, status FROM application").fetchone()
    assert row is not None and row[0] == str(trace) and row[1] == str(shot)
    assert row[2] == "applied", "the frozen record of what was sent is untouched"
