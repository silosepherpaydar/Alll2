"""سنجش احتمال: Brier و کالیبراسیون. نتیجه ناکافی از میانگین کنار گذاشته می‌شود."""

from __future__ import annotations

from collections import defaultdict


def brier_score(pairs: list[tuple[float, float]]) -> float | None:
    """pairs: (probability, outcome in {0, 0.5, 1})."""
    if not pairs:
        return None
    total = 0.0
    for probability, outcome in pairs:
        if not 0 <= probability <= 1:
            raise ValueError("probability out of range")
        if outcome not in {0, 0.5, 1}:
            raise ValueError("outcome must be 0, 0.5, or 1")
        total += (probability - outcome) ** 2
    return total / len(pairs)


def calibration_table(pairs: list[tuple[float, float]], bins: int = 5) -> list[dict[str, float | int]]:
    buckets: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for probability, outcome in pairs:
        index = min(bins - 1, int(probability * bins))
        buckets[index].append((probability, outcome))
    rows = []
    for index in range(bins):
        chunk = buckets.get(index, [])
        if not chunk:
            rows.append(
                {
                    "bin_low": index / bins,
                    "bin_high": (index + 1) / bins,
                    "count": 0,
                    "mean_probability": 0.0,
                    "mean_outcome": 0.0,
                }
            )
            continue
        rows.append(
            {
                "bin_low": index / bins,
                "bin_high": (index + 1) / bins,
                "count": len(chunk),
                "mean_probability": sum(item[0] for item in chunk) / len(chunk),
                "mean_outcome": sum(item[1] for item in chunk) / len(chunk),
            }
        )
    return rows


def outcome_value(status: str) -> float | None:
    if status == "hit":
        return 1.0
    if status == "partial":
        return 0.5
    if status in {"miss", "invalidated"}:
        return 0.0
    return None
