"""Check that every number in an answer appears in this turn's tool results.

Counts like 1,469,070 must match a tool value exactly (+-1) unless visibly rounded
(ending in ,000, or "1.47 million"), which may differ by 0.5%. Percentages may differ
by 0.05 points (or be the fraction form of a tool value).
"""

from __future__ import annotations

import re
from typing import Any

NUM_COMMA = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+)(?:\.\d+)?(?![\d,])")
NUM_MILLION = re.compile(r"(\d+(?:\.\d+)?)\s*million", re.IGNORECASE)
NUM_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", re.IGNORECASE)
TOOL_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def tool_numbers(tool_calls: list[dict[str, Any]]) -> list[float]:
    nums: list[float] = []
    for c in tool_calls:
        for m in TOOL_NUM.findall(str(c.get("result", ""))):
            try:
                nums.append(float(m))
            except ValueError:
                pass
    return nums


def ungrounded_numbers(text: str, tool_calls: list[dict[str, Any]]) -> list[str]:
    pool = tool_numbers(tool_calls)
    bad: list[str] = []
    claims = [
        (m, float(m.replace(",", "")), m.endswith(",000"))
        for m in NUM_COMMA.findall(text)
    ]
    claims += [
        (m + " million", float(m) * 1e6, True) for m in NUM_MILLION.findall(text)
    ]
    for label, v, rounded in claims:
        def tol(x: float, rounded: bool = rounded) -> float:
            return max(1.0, 0.005 * abs(x)) if rounded else 1.0

        if not any(abs(v - x) <= tol(x) for x in pool):
            bad.append(label)
    for m in NUM_PCT.findall(text):
        v = float(m)
        if not any(abs(v - x) <= 0.05 or abs(v / 100 - x) <= 0.0005 for x in pool):
            bad.append(m + "%")
    return bad


PLAIN_VISITS = re.compile(
    r"(?<!injury-related )(?<!injury related )\b(?:ER|emergency(?: room| department)?)"
    r" visits\b",
    re.IGNORECASE,
)


def normalize_visits(text: str) -> str:
    """Project wording: every 'ER visits' is an 'injury-related ER visits'."""
    return PLAIN_VISITS.sub("injury-related ER visits", text)
