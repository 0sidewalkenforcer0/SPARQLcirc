"""Reproducible per-event Bernoulli probabilities for formal experiments.

The mapping is keyed by both a fixed experiment seed and the canonical event
identifier.  It is therefore independent of query order, result order, and
workload sharding: the same event receives the same probability everywhere.
"""

from __future__ import annotations

import hashlib
import math
from typing import Dict, Iterable


DEFAULT_PROBABILITY_SEED = 42
PROBABILITY_SCHEME = "md5-52-event-v1"
PROBABILITY_DOMAIN = "sparqlcirc-event-probability-v1"
PROBABILITY_BITS = 52
PROBABILITY_DENOMINATOR = 1 << PROBABILITY_BITS


def validate_seed(seed: int) -> int:
    """Return a valid non-negative integer seed."""
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("probability seed must be a non-negative integer")
    return seed


def probability_payload(event_id: str, seed: int) -> str:
    """Return the exact text mapped to a pseudorandom probability."""
    validate_seed(seed)
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event identifier must be a non-empty string")
    return "%s|%d|%s" % (PROBABILITY_DOMAIN, seed, event_id)


def event_probability(event_id: str, seed: int = DEFAULT_PROBABILITY_SEED) -> float:
    """Map one event identifier to a deterministic uniform value in ``(0, 1)``.

    MD5 is used only as a portable pseudorandom mixer, not for authentication
    or identity.  Taking 52 bits lets PostgreSQL and Python reproduce the same
    value exactly before the midpoint conversion to a binary64 probability.
    """
    payload = probability_payload(event_id, seed).encode("utf-8")
    digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    sample = int(digest[: PROBABILITY_BITS // 4], 16)
    probability = (sample + 0.5) / PROBABILITY_DENOMINATOR
    if not math.isfinite(probability) or not 0.0 < probability < 1.0:
        raise AssertionError("seeded event probability is outside (0, 1)")
    return probability


def event_weights(
    event_ids: Iterable[str], seed: int = DEFAULT_PROBABILITY_SEED
) -> Dict[str, float]:
    """Return reproducible probabilities for the distinct supplied events."""
    validate_seed(seed)
    return {event_id: event_probability(event_id, seed) for event_id in event_ids}
