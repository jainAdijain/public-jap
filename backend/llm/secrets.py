"""A user's own OpenRouter key, encrypted at rest (IDEA-19).

A key a person pastes in is theirs, and it spends their money, so it is not kept where
reading the database is enough to use it. Each one is sealed with AES-GCM under a master
key held only in the environment (JAP_SECRET_KEY): a fresh 12-byte nonce per write, the
user and kind bound in as associated data so a row cannot be moved to another account and
still open, and the tag AES-GCM already carries as the check that it was not edited.

JAP_SECRET_KEY is 32 bytes, base64. Generate one with:

    python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"

Without it the feature is simply off (configured() is False): the API says so and nobody
can save a key, which is better than saving keys that a restart could not read back. Losing
the master key loses every saved key — users re-paste theirs; nothing else is affected.
"""

import base64
import logging
import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import UserSecret

log = logging.getLogger("jap.secrets")
OPENROUTER = "openrouter"

NONCE_BYTES = 12
KEY_BYTES = 32
# What a key must look like before it is worth storing. OpenRouter issues "sk-or-v1-...".
MIN_KEY_LENGTH = 16


class SecretsUnavailable(RuntimeError):
    """JAP_SECRET_KEY is missing or unusable, so nothing can be sealed or opened."""


class CannotOpen(RuntimeError):
    """The stored value did not open: a different master key, or an edited row."""


def master_key() -> bytes:
    raw = get_settings().jap_secret_key.strip()
    if not raw:
        raise SecretsUnavailable("JAP_SECRET_KEY is not set, so saved keys cannot be read")
    try:
        key = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError) as exc:
        raise SecretsUnavailable("JAP_SECRET_KEY is not valid base64") from exc
    if len(key) != KEY_BYTES:
        raise SecretsUnavailable(f"JAP_SECRET_KEY must decode to {KEY_BYTES} bytes")
    return key


def configured() -> bool:
    """Whether keys can be saved and read at all on this stack."""
    try:
        master_key()
    except SecretsUnavailable:
        return False
    return True


def _aad(user_id: uuid.UUID, kind: str) -> bytes:
    """Bound into the seal, so a row copied to another account will not open."""
    return f"{user_id}:{kind}".encode()


def hint_of(secret: str) -> str:
    """What the screen shows instead of the key: the last four characters."""
    return secret[-4:] if len(secret) >= 4 else ""


def seal(secret: str, user_id: uuid.UUID, kind: str) -> tuple[bytes, bytes]:
    """The ciphertext and nonce to store."""
    nonce = os.urandom(NONCE_BYTES)
    box = AESGCM(master_key())
    return box.encrypt(nonce, secret.encode(), _aad(user_id, kind)), nonce


def open_sealed(ciphertext: bytes, nonce: bytes, user_id: uuid.UUID, kind: str) -> str:
    try:
        plain = AESGCM(master_key()).decrypt(bytes(nonce), bytes(ciphertext), _aad(user_id, kind))
    except InvalidTag as exc:
        raise CannotOpen("This saved key could not be read. Paste it again.") from exc
    return plain.decode()


async def has_own_key(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """Whether this account has an OpenRouter key saved that JAP can use, without opening
    it: for the screens that say whose key an account's AI runs on (launch decision D1)."""
    return configured() and await session.get(UserSecret, (user_id, OPENROUTER)) is not None


async def byok_key(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    """This user's own OpenRouter key, or None to use JAP's. Never raises: a key that
    cannot be opened (the master key changed) falls back to JAP's with a warning, because
    failing someone's whole search over it would be worse than paying for one call."""
    row = await session.get(UserSecret, (user_id, OPENROUTER))
    if row is None or not configured():
        return None
    try:
        return open_sealed(row.ciphertext, row.nonce, user_id, OPENROUTER)
    except (CannotOpen, SecretsUnavailable):
        # Imported here, not at the top: the log module reaches llm.redact through its
        # scrubber, so importing it from inside llm would close a circle.
        from observability.logs import event

        event(log, "secrets.unreadable", "A saved API key could not be read",
              level=logging.WARNING)  # fmt: skip
        return None
