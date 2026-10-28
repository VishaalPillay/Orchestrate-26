"""Run configuration that is not a secret: limits and budgets, with environment overrides.

Skeptic budgets are model calls per split. A split whose budget is 0 is not
investigated, and its cases with activity picks are flagged
skeptic_skipped:budget. The test budget is 0: on test the skeptic changed 0 of
the 33 targets it investigated (iteration 13), so skipping it does not alter the
output and keeps `--mode replay` free of provider calls.
"""

from __future__ import annotations

import os
from typing import Final

SKEPTIC_BUDGET_SAMPLE: Final = 60
SKEPTIC_BUDGET_TEST: Final = 0


def skeptic_budgets(overrides: dict[str, int] | None = None) -> dict[str, int]:
    """Per-split budgets: the defaults above, then SKEPTIC_BUDGET_<SPLIT> from the environment, then `overrides`."""
    budgets = {"sample": SKEPTIC_BUDGET_SAMPLE, "test": SKEPTIC_BUDGET_TEST}
    for split in budgets:
        raw = os.environ.get(f"SKEPTIC_BUDGET_{split.upper()}", "").strip()
        if raw:
            budgets[split] = int(raw)
    budgets.update(overrides or {})
    return budgets
