"""Screenshot evidence (PNG), read only through the perception agent.

In deterministic mode the agent is never called. A cached perception result
is still used; without one the file yields no facts, is recorded as
SkippedEvidence, and every case whose audit window includes it is flagged.
Image contents are never guessed.

Perception is not wired up yet: every mode currently skips the image (empty
fact list, SkippedEvidence, warning) so the pipeline stays deterministic.
"""

from __future__ import annotations

from pathlib import Path

from pcda.agents.perception import PerceptionAgent, RawObservation
from pcda.facts import Fact
from pcda.ingest.base import IngestReport, ManifestRow, SkippedEvidence
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
        if self._mode is RunMode.DETERMINISTIC:
            reason = "no perception cache entry (deterministic mode never calls a provider)"
        else:
            reason = "perception not implemented yet"
        self._report.skipped.append(SkippedEvidence(row.file_id, row.captured_at, reason))
        self._report.warnings.append(f"{row.file_id}: image skipped, no facts emitted: {reason}")
        return []
