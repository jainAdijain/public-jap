"""Daily database backup (Phase 8): pg_dump of the whole database into data/backups/.

The file name matches `make backup` (jap-YYYYmmdd-HHMMSS.sql.gz), so `make restore` takes
either. A dump is written to a .part file and renamed when pg_dump succeeds, so a half
written file is never mistaken for a backup. Files older than KEEP_DAYS are removed.

With JAP_BACKUP_AGE_RECIPIENT set the dump is piped through `age` on its way to disk and
lands as .sql.gz.age (IDEA-11). That matters because a dump is the whole database in one
file — every user's resume, answers and contact details — and the copies of it multiply:
the hosting provider's disk snapshots, the daily file here, and whatever `make backup-pull`
leaves on the Mac. Encrypting at the moment of writing means none of those copies is
readable, including the ones on hardware nobody controls. The server is given the public
key only, so the machine that makes the backups cannot read them back; the private key
lives off the server and is what `make restore` asks for.
"""

import asyncio
import os
import shutil
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO
from zoneinfo import ZoneInfo

from sqlalchemy.engine import make_url

from config import get_settings

KEEP_DAYS = 14
PREFIX = "jap-"
SUFFIX = ".sql.gz"
ENCRYPTED_SUFFIX = ".sql.gz.age"
AGE_MISSING = (
    "JAP_BACKUP_AGE_RECIPIENT is set but `age` is not installed in this container, so the "
    "dump would be written in the clear. Nothing was written."
)


class BackupFailed(Exception):
    pass


@dataclass
class Backup:
    path: Path
    at: datetime
    size: int

    @property
    def encrypted(self) -> bool:
        return self.path.name.endswith(ENCRYPTED_SUFFIX)

    def as_dict(self) -> dict[str, object]:
        return {
            "file": self.path.name,
            "at": self.at.isoformat(),
            "bytes": self.size,
            "encrypted": self.encrypted,
        }


def backup_dir() -> Path:
    return Path(get_settings().jap_data_dir) / "backups"


def _when(path: Path) -> datetime | None:
    """The time in a backup's name (local time in JAP_TZ), or None for another file."""
    stem = path.name.removeprefix(PREFIX).removesuffix(ENCRYPTED_SUFFIX).removesuffix(SUFFIX)
    try:
        local = datetime.strptime(stem, "%Y%m%d-%H%M%S")
    except ValueError:
        return None
    return local.replace(tzinfo=ZoneInfo(get_settings().jap_tz))


def backups() -> list[Backup]:
    """Every finished backup, newest first, encrypted or not.

    Both shapes are listed because turning encryption on leaves the dumps made before it
    sitting in the same folder, and they are still restorable backups.
    """
    folder = backup_dir()
    out = []
    for p in folder.glob(f"{PREFIX}*{SUFFIX}*") if folder.exists() else []:
        if p.name.endswith((SUFFIX, ENCRYPTED_SUFFIX)) and (at := _when(p)) is not None:
            out.append(Backup(path=p, at=at, size=p.stat().st_size))
    return sorted(out, key=lambda b: b.at, reverse=True)


def has_today(now: datetime | None = None) -> bool:
    tz = ZoneInfo(get_settings().jap_tz)
    today = (now or datetime.now(UTC)).astimezone(tz).date()
    return any(b.at.astimezone(tz).date() == today and b.size > 0 for b in backups())


def _libpq() -> tuple[str, dict[str, str]]:
    """The database URL for pg_dump, with the password passed in the environment."""
    url = make_url(get_settings().database_url)
    env = {**os.environ}
    if url.password:
        env["PGPASSWORD"] = str(url.password)
    plain = url.set(drivername="postgresql", password=None).render_as_string(hide_password=False)
    return plain, env


async def _write_dump(part: Path, recipient: str) -> None:
    """pg_dump | gzip [| age -r recipient] into part, raising BackupFailed on any failure.

    The compression is done here rather than by gzip.open so that the same loop can feed
    either a file or age's stdin, and so nothing is ever buffered to a plaintext temporary
    file on the way: with a recipient set, the first thing to reach the disk is ciphertext.
    """
    url, env = _libpq()
    try:
        dump = await asyncio.create_subprocess_exec(
            "pg_dump", "--clean", "--if-exists", "--no-owner", f"--dbname={url}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )  # fmt: skip
    except FileNotFoundError as exc:
        raise BackupFailed("pg_dump is not installed in the app container") from exc
    assert dump.stdout is not None and dump.stderr is not None

    age = None
    if recipient:
        try:
            age = await asyncio.create_subprocess_exec(
                "age", "--encrypt", "--recipient", recipient, "--output", str(part),
                stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )  # fmt: skip
        except FileNotFoundError as exc:
            dump.kill()
            await dump.wait()
            raise BackupFailed(AGE_MISSING) from exc

    # 16 + MAX_WBITS asks zlib for a gzip container rather than a bare deflate stream, so
    # the bytes are identical to what gzip.open would have written.
    compress = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    plain = None if age is not None else part.open("wb")
    try:
        while chunk := await dump.stdout.read(1 << 16):
            await _put(compress.compress(chunk), age, plain)
        await _put(compress.flush(), age, plain)
    finally:
        if plain is not None:
            plain.close()
        if age is not None and age.stdin is not None:
            age.stdin.close()

    err = (await dump.stderr.read()).decode(errors="replace").strip()
    if await dump.wait() != 0:
        raise BackupFailed(f"pg_dump failed: {err.splitlines()[-1] if err else 'no output'}")
    if age is not None:
        assert age.stderr is not None
        age_err = (await age.stderr.read()).decode(errors="replace").strip()
        if await age.wait() != 0:
            last = age_err.splitlines()[-1] if age_err else "no output"
            raise BackupFailed(f"age failed: {last}")


async def _put(
    data: bytes, age: asyncio.subprocess.Process | None, plain: IO[bytes] | None
) -> None:
    if not data:
        return
    if age is not None:
        assert age.stdin is not None
        age.stdin.write(data)
        await age.stdin.drain()
    else:
        assert plain is not None
        plain.write(data)


async def run_backup(now: datetime | None = None) -> Backup:
    """Dump the database now and prune old files."""
    settings = get_settings()
    tz = ZoneInfo(settings.jap_tz)
    at = (now or datetime.now(UTC)).astimezone(tz)
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    recipient = settings.jap_backup_age_recipient.strip()
    suffix = ENCRYPTED_SUFFIX if recipient else SUFFIX
    final = folder / f"{PREFIX}{at:%Y%m%d-%H%M%S}{suffix}"
    part = final.with_name(final.name + ".part")
    try:
        await _write_dump(part, recipient)
    except BackupFailed:
        part.unlink(missing_ok=True)
        raise
    part.rename(final)
    prune(at)
    return Backup(path=final, at=at, size=final.stat().st_size)


def age_ready() -> tuple[bool, str]:
    """Whether encrypted backups are configured and could actually be written (Health).

    A recipient with no `age` to run it is the dangerous combination: without this check
    the first sign of it is the daily backup failing, or worse, not failing.
    """
    recipient = get_settings().jap_backup_age_recipient.strip()
    if not recipient:
        return False, "Off: dumps are written in the clear (set JAP_BACKUP_AGE_RECIPIENT on live)"
    if shutil.which("age") is None:
        return False, AGE_MISSING
    return True, f"On: dumps are encrypted to {recipient[:12]}…"


def prune(now: datetime) -> list[str]:
    """Remove backups older than KEEP_DAYS (and .part leftovers); the newest always stays."""
    removed = []
    keep_from = now - timedelta(days=KEEP_DAYS)
    for i, b in enumerate(backups()):
        if i > 0 and b.at < keep_from:
            b.path.unlink(missing_ok=True)
            removed.append(b.path.name)
    for leftover in backup_dir().glob("*.part"):
        if datetime.fromtimestamp(leftover.stat().st_mtime, UTC) < now - timedelta(hours=1):
            leftover.unlink(missing_ok=True)
    return removed
