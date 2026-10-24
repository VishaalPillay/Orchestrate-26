"""Pipe-delimited text records (customer_account_handover):

    - timestamp | system | resource_id | record_type | state | subject

Same vocabulary as the JSON ledgers (FOUND, NOT_FOUND, IDENTIFIABLE, REDACTED,
ANONYMIZED, ACTIVE, DISABLED, BLOCKED). The `Snapshot:` header is checked
against the manifest; the manifest wins.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from pcda.category import CATEGORIES, DATA_FLOW
from pcda.facts import ATTR_ID_FIELDS, ATTR_PII_FIELDS, ATTR_STATE, Fact, parse_ts
from pcda.ingest.base import IngestReport, ManifestRow
from pcda.ingest.common import Emitter, result_to_kind, view_record

_SNAPSHOT: Final = re.compile(r"^Snapshot:\s*(\S+)", re.M)
_LABELS: Final = frozenset(CATEGORIES) | {DATA_FLOW}


class TxtAdapter:
    def __init__(self, report: IngestReport | None = None) -> None:
        self._report = report

    def parse(self, path: Path, row: ManifestRow) -> list[Fact]:
        text = path.read_text(encoding="utf-8")
        em = Emitter(row, self._report, src="ledger")
        snap = _SNAPSHOT.search(text)
        em.check_capture(snap.group(1) if snap else None, "Snapshot header")
        for n, line in enumerate(text.splitlines(), start=1):
            if not line.startswith("- "):
                continue
            parts = [p.strip() for p in line[2:].split("|")]
            if len(parts) != 6:
                em.warn(f"line {n}: expected 6 pipe-delimited columns, got {len(parts)}")
                continue
            ts, system, rid, record_type, state, subject = parts
            mapped = result_to_kind(record_type, state)
            if mapped is None:
                em.warn(f"line {n}: unmapped state {state!r} for {rid}")
                continue
            kind, word = mapped
            view = view_record({"subject_reference": subject})
            em.emit(
                kind,
                parse_ts(ts),
                system_id=system,
                resource_id=rid,
                category=record_type if record_type in _LABELS else None,
                identifiers=view.identifiers,
                table="handover",
                source_line=n,
                attrs={
                    ATTR_STATE: word,
                    ATTR_PII_FIELDS: ",".join(view.pii_fields) or None,
                    ATTR_ID_FIELDS: ",".join(view.id_fields) or None,
                    "record_type": record_type,
                },
            )
        return em.facts
