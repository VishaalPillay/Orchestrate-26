"""Adapter protocol and the manifest row every adapter receives."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pcda import paths
from pcda.facts import Fact, parse_ts


@dataclass(frozen=True)
class ManifestRow:
    file_id: str
    file_path: str
    origin: str
    captured_at: datetime

    @property
    def absolute_path(self) -> Path:
        return paths.DATASET / self.file_path


@dataclass(frozen=True)
class SkippedEvidence:
    """Evidence that produced no facts by design (e.g. image without cache in deterministic mode)."""

    evidence_id: str
    captured_at: datetime
    reason: str


@dataclass
class IngestReport:
    skipped: list[SkippedEvidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # image vs text evidence disagreements


class Adapter(Protocol):
    def parse(self, path: Path, row: ManifestRow) -> list[Fact]:
        """Return every Fact in the file. Must not consult other files or case context."""
        ...


def read_manifest(path: Path = paths.EVIDENCE_MANIFEST) -> list[ManifestRow]:
    with path.open(encoding="utf-8", newline="") as fh:
        rows = [
            ManifestRow(r["file_id"], r["file_path"], r["origin"], parse_ts(r["captured_at"]))
            for r in csv.DictReader(fh)
        ]
    rows.sort(key=lambda r: r.file_id)
    return rows
