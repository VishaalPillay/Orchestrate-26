"""Perception agent: reads screenshots (and unusable PDF text layers).

It returns raw observations only. Code in the adapters turns an observation
into a Fact after grounding it: every resource_id, identifier and timestamp
must parse and, for text inputs, appear verbatim in the source.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pcda.ingest.base import ManifestRow
from pcda.llm.client import LLMClient


@dataclass(frozen=True)
class RawObservation:
    evidence_id: str
    system_id: str | None
    resource_id: str | None
    field: str  # e.g. "email", "phone", "status", "restore_allowed", "timestamp"
    value: str
    quote: str  # on-screen or in-text span the value was read from


class PerceptionAgent(Protocol):
    def extract_image(self, path: Path, row: ManifestRow) -> list[RawObservation]: ...

    def extract_text(self, text: str, row: ManifestRow) -> list[RawObservation]: ...


class LLMPerceptionAgent:
    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def extract_image(self, path: Path, row: ManifestRow) -> list[RawObservation]:
        raise NotImplementedError("perception.LLMPerceptionAgent.extract_image")

    def extract_text(self, text: str, row: ManifestRow) -> list[RawObservation]:
        raise NotImplementedError("perception.LLMPerceptionAgent.extract_text")
