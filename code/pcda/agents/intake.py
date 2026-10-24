"""Intake agent: does request_text authorize deletion?

Its answer is an input to the solver, not a status. The solver alone maps
it to NOT_APPLICABLE or to an audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pcda.llm.client import LLMClient


@dataclass(frozen=True)
class IntakeResult:
    case_id: str
    authorizes_deletion: bool | None  # None: undecided, the case is flagged
    language: str | None
    request_kind: str  # e.g. "DELETION", "ACCESS", "CORRECTION", "EXPORT", "MARKETING_OPT_OUT"
    scope_hints: tuple[str, ...] = ()  # e.g. "retain_backups", "anonymous_stats_ok"
    rationale: str = ""


class IntakeAgent(Protocol):
    def classify(self, case_id: str, request_text: str) -> IntakeResult: ...


class LLMIntakeAgent:
    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def classify(self, case_id: str, request_text: str) -> IntakeResult:
        raise NotImplementedError("intake.LLMIntakeAgent.classify")
