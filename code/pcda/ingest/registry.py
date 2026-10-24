"""Adapter registry: dispatch by file extension into one global FactStore.

Kept out of ingest/__init__.py so importing ingest.base never pulls in the
adapters (perception imports ingest.base; the PDF and image adapters import
perception)."""

from __future__ import annotations

from pcda.agents.perception import PerceptionAgent
from pcda.facts import FactStore
from pcda.ingest.base import Adapter, IngestReport, ManifestRow
from pcda.ingest.image_adapter import ImageAdapter
from pcda.ingest.json_adapter import JsonAdapter
from pcda.ingest.log_adapter import LogAdapter
from pcda.ingest.pdf_adapter import PdfAdapter
from pcda.ingest.sql_adapter import SqlAdapter
from pcda.ingest.txt_adapter import TxtAdapter
from pcda.llm.client import RunMode


def build_adapters(
    perception: PerceptionAgent, mode: RunMode, report: IngestReport
) -> dict[str, Adapter]:
    return {
        ".json": JsonAdapter(report),
        ".log": LogAdapter(report),
        ".sql": SqlAdapter(report),
        ".txt": TxtAdapter(report),
        ".pdf": PdfAdapter(perception, mode, report),
        ".png": ImageAdapter(perception, mode, report),
    }


def ingest_all(
    manifest: list[ManifestRow], adapters: dict[str, Adapter], report: IngestReport
) -> FactStore:
    """Parse every manifest file into one global FactStore. Case-independent."""
    store = FactStore()
    for row in manifest:
        path = row.absolute_path
        adapter = adapters.get(path.suffix.lower())
        if adapter is None:
            raise ValueError(f"no adapter for {row.file_id} ({path.suffix})")
        for fact in adapter.parse(path, row):
            store.add(fact)
    return store
