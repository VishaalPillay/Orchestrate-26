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
    perception: PerceptionAgent,
    mode: RunMode,
    report: IngestReport,
    known_prefixes: frozenset[str] = frozenset(),
    file_links: dict[str, tuple[tuple[str, str], ...]] | None = None,
) -> dict[str, Adapter]:
    """`known_prefixes`: resource-id prefixes from system_activity, for image grounding."""
    return {
        ".json": JsonAdapter(report),
        ".log": LogAdapter(report),
        ".sql": SqlAdapter(report),
        ".txt": TxtAdapter(report),
        ".pdf": PdfAdapter(perception, mode, report),
        ".png": ImageAdapter(perception, mode, report, known_prefixes, file_links=file_links or {}),
    }


def ingest_all(
    manifest: list[ManifestRow], adapters: dict[str, Adapter], report: IngestReport
) -> FactStore:
    """Parse every manifest file into one global FactStore. Case-independent.

    Two passes: every text file first, then images, so image grounding knows the
    resource prefixes of the text evidence and can cross-check identifiers.
    """
    store = FactStore()
    images: list[tuple[ManifestRow, ImageAdapter]] = []
    for row in manifest:
        path = row.absolute_path
        adapter = adapters.get(path.suffix.lower())
        if adapter is None:
            raise ValueError(f"no adapter for {row.file_id} ({path.suffix})")
        if isinstance(adapter, ImageAdapter):
            images.append((row, adapter))
            continue
        for fact in adapter.parse(path, row):
            store.add(fact)
    bound: set[int] = set()
    for row, adapter in images:
        if id(adapter) not in bound:
            adapter.bind_text_store(FactStore(list(store)))
            bound.add(id(adapter))
        facts = adapter.parse(row.absolute_path, row)
        adapter.cross_check(facts)
        for fact in facts:
            store.add(fact)
    return store
