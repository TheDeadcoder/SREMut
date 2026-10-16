"""Small exact statistics for counts: Wilson intervals, Cohen's kappa and McNemar's exact test."""
from collections import Counter
from math import comb, sqrt


def wilson(k, n, z=1.959964):
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(k, n):
    interval = wilson(k, n)
    return {"k": k, "n": n, "rate": round(k / n, 3) if n else None,
            "wilson95": [round(x, 3) for x in interval] if interval else None}


def cohen_kappa(pairs):
    pairs = list(pairs)
    if not pairs:
        return None
    observed = sum(a == b for a, b in pairs) / len(pairs)
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(first[c] * second[c] for c in first) / len(pairs) ** 2
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def mcnemar_exact(b, c):
    """Two-sided exact p from the discordant pairs b and c."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, x) for x in range(0, min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)
