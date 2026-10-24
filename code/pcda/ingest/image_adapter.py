"""Screenshot evidence (PNG), read only through the perception agent.

In deterministic mode the agent is never called. A cached perception result
is still used; without one the file yields no facts, is recorded as
SkippedEvidence, and every case whose audit window includes it is flagged.
Image contents are never guessed.
"""

from __future__ import annotations

from pathlib import Path

from pcda.agents.perception import PerceptionAgent, RawObservation
from pcda.facts import Fact
from pcda.ingest.base import IngestReport, ManifestRow
from pcda.llm.client import RunMode


def ground_observations(observations: list[RawObservation], row: ManifestRow) -> list[Fact]:
    """Turn observations into Facts; drop any that fail identifier/timestamp parsing."""
    raise NotImplementedError("image_adapter.ground_observations")


class ImageAdapter:
    def __init__(self, perception: PerceptionAgent, mode: RunMode, report: IngestReport) -> None:
        self._perception = perception
        self._mode = mode
        self._report = report

    def parse(self, path: Path, row: ManifestRow) -> list[Fact]:
        raise NotImplementedError("image_adapter.ImageAdapter.parse")
