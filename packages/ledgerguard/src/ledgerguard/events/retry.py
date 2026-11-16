"""Where a failed message goes next: the retry topic with backoff, or the DLQ.

Transient failures (database, Redis, network) are retried with exponential backoff
and full jitter, at most ``MAX_ATTEMPTS`` times in total. Poison messages skip retries:
retrying a message that can never succeed only delays everything behind it.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ledgerguard.events.schemas import PoisonMessageError

MAX_ATTEMPTS = 5
BASE_DELAY_S = 1.0
MAX_DELAY_S = 60.0


@dataclass(frozen=True, slots=True)
class Route:
    destination: Literal["retry", "dlq"]
    next_attempt: int
    delay_s: float
    reason: str


def backoff_s(
    attempt: int, rand: Callable[[], float] = random.random, base_delay_s: float = BASE_DELAY_S
) -> float:
    """Full-jitter delay before the retry after ``attempt`` (1-based).

    Uniform in [0, min(MAX_DELAY_S, BASE_DELAY_S * 2^(attempt-1))).
    """
    ceiling = min(MAX_DELAY_S, base_delay_s * 2.0 ** (attempt - 1))
    return float(rand() * ceiling)


def route_failure(
    attempt: int,
    error: BaseException,
    rand: Callable[[], float] = random.random,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    base_delay_s: float = BASE_DELAY_S,
) -> Route:
    reason = f"{type(error).__name__}: {error}"[:500]
    if isinstance(error, PoisonMessageError):
        return Route("dlq", attempt, 0.0, f"poison: {reason}")
    if attempt >= max_attempts:
        return Route("dlq", attempt, 0.0, f"retries exhausted after {attempt}: {reason}")
    return Route("retry", attempt + 1, backoff_s(attempt, rand, base_delay_s), reason)
