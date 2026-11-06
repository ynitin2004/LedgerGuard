"""API key generation and hashing (plan A1).

Only the SHA-256 hash is stored. A plain hash (no salt, no KDF) is appropriate here
because keys are 256-bit random values, not human passwords: they cannot be guessed
or dictionary-attacked, and a deterministic hash allows an indexed lookup.
"""

from __future__ import annotations

import hashlib
import secrets

API_KEY_PREFIX = "lg_"


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
