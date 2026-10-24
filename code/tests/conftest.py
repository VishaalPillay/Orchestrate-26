"""Shared fixtures. Evidence snippets in these tests are copied verbatim from dataset/evidence."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pcda.facts import parse_ts  # noqa: E402
from pcda.ingest.base import IngestReport, ManifestRow  # noqa: E402


@pytest.fixture
def report() -> IngestReport:
    return IngestReport()


@pytest.fixture
def manifest_row() -> Callable[..., ManifestRow]:
    def make(file_id: str, captured_at: str, origin: str = "test_origin") -> ManifestRow:
        return ManifestRow(file_id, f"evidence/{file_id}", origin, parse_ts(captured_at))

    return make


@pytest.fixture
def write_evidence(tmp_path: Path) -> Callable[[str, str], Path]:
    def write(name: str, text: str) -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return write
