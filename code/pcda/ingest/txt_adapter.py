"""Pipe-delimited text records (customer_account_handover):

    timestamp | system | resource_id | record_type | state | subject
"""

from __future__ import annotations

from pathlib import Path

from pcda.facts import Fact
from pcda.ingest.base import ManifestRow


class TxtAdapter:
    def parse(self, path: Path, row: ManifestRow) -> list[Fact]:
        raise NotImplementedError("txt_adapter.TxtAdapter.parse")
