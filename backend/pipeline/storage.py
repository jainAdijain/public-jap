"""Where files live under data/ (PRD S7): each user's resumes, cover letters, screenshots
and traces in a folder of their own, data/users/<user id>/<kind>/.

A file is never served by its path. A route finds the row that names the file (a resume
version, an application), checks that row belongs to the signed-in user, and only then
serves what the row points at, provided it is still inside data/. Files written before each
user had a folder keep their old paths and are served the same way, by their owning row.
"""

import uuid
from pathlib import Path

from config import get_settings


def root() -> Path:
    return Path(get_settings().jap_data_dir)


def user_dir(user_id: uuid.UUID) -> Path:
    return root() / "users" / str(user_id)


def user_path(user_id: uuid.UUID, kind: str, name: str) -> Path:
    """data/users/<user>/<kind>/<name>, with the folder made."""
    folder = user_dir(user_id) / kind
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def served(path: str | None) -> Path | None:
    """The file a row points at, when it is inside data/ and still there; else None."""
    if not path:
        return None
    resolved = Path(path).resolve()
    return resolved if resolved.is_relative_to(root().resolve()) and resolved.is_file() else None
