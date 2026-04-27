"""Repetition-loop detector for vision-model responses.

Detects when a vision LLM has entered a degenerate loop of repeating the same
n-gram phrase, and extracts any valid prefix that appeared before the loop.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoopDetectionResult:
    is_loop: bool
    salvaged_prefix: str | None
    repeated_phrase: str | None
    repetition_count: int


def detect_repetition_loop(
    text: str,
    *,
    min_window: int = 3,
    max_window: int = 8,
    threshold: int = 5,
) -> LoopDetectionResult:
    """Detect whether *text* contains a repetition loop.

    Splits by whitespace and scans with sliding windows from *max_window*
    tokens down to *min_window* (larger windows first, so more informative
    patterns are preferred over trivial ones).  A loop is detected when the
    same n-gram appears *threshold* or more times consecutively.

    Returns a :class:`LoopDetectionResult` describing the outcome.  If a loop
    is found and the text before it is at least 20 characters, that prefix is
    stored in *salvaged_prefix*; otherwise *salvaged_prefix* is ``None``.
    """
    tokens = text.split()
    if len(tokens) < min_window * threshold:
        return LoopDetectionResult(
            is_loop=False,
            salvaged_prefix=None,
            repeated_phrase=None,
            repetition_count=0,
        )

    for n in range(max_window, min_window - 1, -1):
        max_start = len(tokens) - n * threshold
        if max_start < 0:
            continue
        for i in range(max_start + 1):
            window = tokens[i : i + n]
            count = 1
            j = i + n
            while j + n <= len(tokens) and tokens[j : j + n] == window:
                count += 1
                j += n
            if count >= threshold:
                prefix = " ".join(tokens[:i]).strip()
                salvaged_prefix = prefix if len(prefix) >= 20 else None
                return LoopDetectionResult(
                    is_loop=True,
                    salvaged_prefix=salvaged_prefix,
                    repeated_phrase=" ".join(window),
                    repetition_count=count,
                )

    return LoopDetectionResult(
        is_loop=False,
        salvaged_prefix=None,
        repeated_phrase=None,
        repetition_count=0,
    )
