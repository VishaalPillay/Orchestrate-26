"""Run configuration that is not a secret: every threshold, limit and budget in one place.

Secrets, provider endpoints and model names are never here: they come from the
environment or the git-ignored .env (LLM_PROVIDERS, <NAME>_BASE_URL,
<NAME>_API_KEY, INTAKE_MODEL, CATEGORY_MODEL, PERCEPTION_MODEL, SKEPTIC_MODEL,
GROQ_FALLBACK_MODEL). File locations are in paths.py. The modules that use a
value below import it from here under their own historical name.

Skeptic budgets are model calls per split. A split whose budget is 0 is not
investigated, and its cases with activity picks are flagged
skeptic_skipped:budget. The test budget is 0: on test the skeptic changed 0 of
the 33 targets it investigated (iteration 13), so skipping it does not alter the
output and keeps `--mode replay` free of provider calls.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Final

# --- attribution ----------------------------------------------------------------------------------
# activity_temporal gate: |event_time - reported_at| must be within this. The five sample
# accounts found only in system_activity sit 0.3-8.0 minutes from reported_at; the nearest
# wrong candidate is 44 minutes away.
ACTIVITY_WINDOW: Final = timedelta(minutes=15)

# --- LLM client -----------------------------------------------------------------------------------
# Model names (not secrets). The environment variable of the same name overrides the default. The defaults are the
# models that produced the cached answers, so a clean checkout without .env reproduces the cache keys in replay.
MODEL_DEFAULTS: Final[dict[str, str]] = {
    "INTAKE_MODEL": "gemini-3.5-flash-lite",
    "CATEGORY_MODEL": "gemini-3.5-flash-lite",
    "PERCEPTION_MODEL": "gemini-3.6-flash",
    "SKEPTIC_MODEL": "gemini-3.5-flash-lite",
}
LLM_MAX_ATTEMPTS: Final = 3  # per call, honoring Retry-After
LLM_TIMEOUT_S_DEFAULT: Final = "120"  # LLM_TIMEOUT_S overrides
LLM_RPM_LIMIT_DEFAULT: Final = "10"  # LLM_RPM_LIMIT overrides
INTAKE_BATCH_SIZE: Final = 20  # requests per intake call; fixed so cache keys never depend on the split

# --- skeptic --------------------------------------------------------------------------------------
SKEPTIC_BUDGET_SAMPLE: Final = 60
SKEPTIC_BUDGET_TEST: Final = 0
SKEPTIC_MAX_CALLS_PER_TARGET: Final = 8
SKEPTIC_MAX_GATE_RETRIES: Final = 2  # rejected claims returned to the agent before "no change"
SKEPTIC_TOOL_CAP: Final = 1500  # characters per tool result (Groq free tier: 8K tokens a minute)
# Largest |row - reported_at| among the samples' activity picks, in minutes; picks beyond it go first.
SKEPTIC_SAMPLE_ENVELOPE_MIN: Final = 74.1
# main.py refuses to write output.csv when the skeptic changes more than this (override with a flag).
BLAST_MAX_VERDICTS: Final = 10
BLAST_MAX_STEPS: Final = 25


def skeptic_budgets(overrides: dict[str, int] | None = None) -> dict[str, int]:
    """Per-split budgets: the defaults above, then SKEPTIC_BUDGET_<SPLIT> from the environment, then `overrides`."""
    budgets = {"sample": SKEPTIC_BUDGET_SAMPLE, "test": SKEPTIC_BUDGET_TEST}
    for split in budgets:
        raw = os.environ.get(f"SKEPTIC_BUDGET_{split.upper()}", "").strip()
        if raw:
            budgets[split] = int(raw)
    budgets.update(overrides or {})
    return budgets
