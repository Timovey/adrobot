"""Equal redistribution of offer shares with pinned weights kept fixed."""

from __future__ import annotations

import math


def redistribute(offers: list[dict]) -> list[dict]:
    """
    Recalculate `share` for active offers.

    - Removed offers always get share 0.
    - Pinned active offers keep their (integer-rounded) share; if pinned shares
      exceed 100 they are scaled down proportionally to exactly 100.
    - Remaining share (100 - sum(pinned)) is split equally among unpinned active offers.
    Shares are integers that sum to exactly 100 (or 0 if nothing active).
    """
    active = [o for o in offers if o.get("status", "active") == "active"]
    removed = [o for o in offers if o.get("status") == "removed"]

    for o in removed:
        o["share"] = 0

    if not active:
        return offers

    pinned = [o for o in active if o.get("pinned")]
    free = [o for o in active if not o.get("pinned")]

    pinned_shares = [int(round(min(max(float(o.get("share") or 0), 0.0), 100.0))) for o in pinned]

    if not free or sum(pinned_shares) > 100:
        # Only pinned offers, or pinned overflow: scale pinned to exactly 100.
        pinned_shares = normalize_to_100(pinned_shares)
    for o, s in zip(pinned, pinned_shares):
        o["share"] = s

    if not free:
        return offers

    remaining = 100 - sum(pinned_shares)
    for o, s in zip(free, _equal_split(remaining, len(free))):
        o["share"] = s

    return offers


def normalize_to_100(values: list[float]) -> list[int]:
    """
    Scale non-negative values to integers summing to exactly 100
    (largest-remainder rounding; ties go to the earlier item).
    All-zero input is split equally. Empty input stays empty.
    """
    if not values:
        return []
    clean = [max(float(v or 0), 0.0) for v in values]
    total = sum(clean)
    if total <= 0:
        return _equal_split(100, len(clean))
    exact = [v * 100.0 / total for v in clean]
    floors = [math.floor(x) for x in exact]
    leftover = 100 - sum(floors)
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[:leftover]:
        floors[i] += 1
    return floors


def _equal_split(total: int, n: int) -> list[int]:
    if n <= 0:
        return []
    total = max(int(total), 0)
    base, rem = divmod(total, n)
    return [base + 1 if i < rem else base for i in range(n)]
