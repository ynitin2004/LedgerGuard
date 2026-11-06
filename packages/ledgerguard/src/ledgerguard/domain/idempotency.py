"""Canonical request hashing for idempotency keys (docs/API_SPEC.md)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


def canonical_json(payload: Mapping[str, object]) -> str:
    """JSON with sorted keys and no insignificant whitespace.

    Two requests with the same meaning produce the same string regardless of field
    order or formatting. Callers normalise values first (e.g. upper-case currency).
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_hash(payload: Mapping[str, object]) -> str:
    """SHA-256 hex digest of the canonical JSON."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
