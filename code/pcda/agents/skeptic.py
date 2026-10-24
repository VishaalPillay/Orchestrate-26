"""Skeptic agent: challenges a Certificate. Advisory only.

An objection can flag a case or ask the solver to demote an attribution; it
can never add a step, change a status, or supply a citation. Every
activity_temporal attribution (needs_skeptic=True) is sent here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pcda.identity import AttributedResource
from pcda.llm.client import LLMClient
from pcda.solver import Certificate


@dataclass(frozen=True)
class Objection:
    case_id: str
    target: str  # resource_id or obligation reference
    kind: str  # e.g. "WEAK_ATTRIBUTION", "MISSING_COPY", "TEMPORAL_DOUBT"
    message: str


class SkepticAgent(Protocol):
    def challenge(
        self, certificate: Certificate, attributions: list[AttributedResource]
    ) -> list[Objection]: ...


class LLMSkepticAgent:
    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def challenge(
        self, certificate: Certificate, attributions: list[AttributedResource]
    ) -> list[Objection]:
        raise NotImplementedError("skeptic.LLMSkepticAgent.challenge")
