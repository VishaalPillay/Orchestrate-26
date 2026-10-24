"""PDF evidence. The pymupdf text layer is the deterministic primary path.

Perception is a fallback only when the text layer is unusable (empty, or too
scrambled to locate resource_ids and identifiers). Recon found the PDF text
layout-scrambled but readable ("captured 12 Sep 2026 07 30 UTC").
"""

from __future__ import annotations

from pathlib import Path

from pcda.agents.perception import PerceptionAgent
from pcda.facts import Fact
from pcda.ingest.base import IngestReport, ManifestRow
from pcda.llm.client import RunMode


def extract_text(path: Path) -> str:
    import pymupdf  # lazy: only needed when PDFs are ingested

    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


def is_text_usable(text: str) -> bool:
    raise NotImplementedError("pdf_adapter.is_text_usable")


class PdfAdapter:
    def __init__(self, perception: PerceptionAgent, mode: RunMode, report: IngestReport) -> None:
        self._perception = perception
        self._mode = mode
        self._report = report

    def parse(self, path: Path, row: ManifestRow) -> list[Fact]:
        raise NotImplementedError("pdf_adapter.PdfAdapter.parse")
