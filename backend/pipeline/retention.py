"""How long JAP keeps the files it makes while applying (IDEA-14).

Three kinds of file pile up on their own, with nobody ever asking for the old ones:

- a Playwright trace, a few megabytes per application, only worth having while a failure
  from the last few days is being looked into (JAP_KEEP_TRACES_DAYS, 30);
- the confirmation screenshot, a few hundred kilobytes, and the only evidence of what was
  actually sent to an employer, so it is kept far longer (JAP_KEEP_SCREENSHOTS_DAYS, 180);
- the weekly form test's screenshots in data/form_checks, which are JAP's own and belong to
  no user, kept as long as a screenshot.

Resumes and cover letters are **not** here and never will be: they are the user's own
documents, and the only thing that removes them is deleting the account.

Only files are deleted; no row is touched. An Applied application is frozen — the database
refuses every change to one but `email_sent_at` (PRD A2, the `application_frozen` trigger) —
and those are exactly the rows that have a trace and a screenshot. Setting the path back to
null would be a change, so the record of what was sent stays exactly as it was and the path
it holds becomes a path to a file that is gone. Nothing reads it as a promise: both screens
ask the filesystem, not the column (`_exists` in api/applications.py, `Path(...).exists()`
in api/ops.py), and `storage.served` answers 404 for a file that is not there. So the job
says "no trace" rather than offering one that fails.

The sweep is a step of health-watch, hourly, next to the log cleanup.
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from config import get_settings
from observability.logs import event
from pipeline import storage

log = logging.getLogger(__name__)


def _older_than(folder: Path, days: int, now: datetime) -> list[Path]:
    if not folder.is_dir():
        return []
    cutoff = now - timedelta(days=days)
    out = []
    for f in folder.iterdir():
        try:
            if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime, UTC) < cutoff:
                out.append(f)
        except OSError:
            continue
    return out


def expired(kind: str, days: int, now: datetime) -> list[Path]:
    """Files of one kind past their time, across every user's folder (or JAP's own)."""
    if days <= 0:  # 0 or less: keep for ever
        return []
    root = storage.root()
    if kind == "form_checks":
        return _older_than(root / "form_checks", days, now)
    users = root / "users"
    folders = sorted(users.glob(f"*/{kind}")) if users.is_dir() else []
    return [f for folder in folders for f in _older_than(folder, days, now)]


def _delete(paths: list[Path]) -> int:
    gone = 0
    for p in paths:
        try:
            p.unlink()
        except OSError:
            continue
        gone += 1
    return gone


def sweep(now: datetime | None = None) -> dict[str, int]:
    """Delete what is past its time. Counts, for the health-watch step's result."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    counts = {
        "traces": _delete(expired("traces", settings.jap_keep_traces_days, now)),
        "screenshots": _delete(expired("screenshots", settings.jap_keep_screenshots_days, now)),
        "form_checks": _delete(expired("form_checks", settings.jap_keep_screenshots_days, now)),
    }
    if any(counts.values()):
        event(
            log,
            "retention.swept",
            f"Deleted files past their time: {counts}",
            traces=counts["traces"],
            screenshots=counts["screenshots"],
            form_checks=counts["form_checks"],
        )
    return counts
