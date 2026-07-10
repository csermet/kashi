"""API key primitives. Keys are 128-bit random ("ksh_" + 32 hex chars), so a
plain SHA-256 lookup hash is sufficient — bcrypt/scrypt would only slow down
every request without adding security against offline attacks on random keys.
Raw keys are shown exactly once at creation and never logged or persisted.
"""

import hashlib
import secrets

KEY_PREFIX = "ksh_"


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_hex(16)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def looks_like_key(value: str) -> bool:
    return (
        value.startswith(KEY_PREFIX)
        and len(value) == len(KEY_PREFIX) + 32
        and all(c in "0123456789abcdef" for c in value[len(KEY_PREFIX) :])
    )
