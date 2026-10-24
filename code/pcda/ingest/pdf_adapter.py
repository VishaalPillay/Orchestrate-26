"""PDF evidence. The pymupdf text layer is the deterministic primary path.

Perception is a fallback only when the text layer is unusable (empty, or too
scrambled to locate resource_ids and identifiers). The fallback is not wired
up yet: an unusable PDF is recorded as SkippedEvidence and yields no facts.
Recon found the four PDFs layout-scrambled but readable ("captured 12 Sep
2026 07 30 UTC": the colon of a time is a line break).

A PDF is a business artifact, not a log: the fact observed_at is the manifest
captured_at (the moment the document showed this record), and the dates in
the text (recorded, submitted, retain-until) are kept as attrs. System id is
the manifest origin. Per document:
  * resource ids found in the text (support_001, audit_record_003) give one
    PRESENCE fact each, carrying every identifier in the document;
  * register rows (`ORD-... | user_08 | reason | date | amount | Retain until
    ...`) give one PRESENCE fact each, keyed by identifier and order_reference
    with resource_id None (the register does not name a resource);
  * otherwise a single document-level fact.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from pcda.agents.perception import PerceptionAgent
from pcda.facts import ATTR_ID_FIELDS, ATTR_PII_FIELDS, ATTR_STATE, Fact, FactKind
from pcda.ingest.base import IngestReport, ManifestRow, SkippedEvidence
from pcda.ingest.common import Emitter, normalize_value
from pcda.llm.client import RunMode

_MONTHS: Final = {
    m: i
    for i, m in enumerate(
        "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), start=1
    )
}
_DATE: Final = (
    r"(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4})"
    r"(?:\s+(\d{2})\s+(\d{2}) UTC)?"
)
_EMAIL: Final = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE: Final = re.compile(r"\+\d[\d ]{6,}\d")
_USER: Final = re.compile(r"\buser_\d+\b")
_RESOURCE: Final = re.compile(r"\b[a-z]+(?:_[a-z]+)*_\d+\b")
_ORD_ROW: Final = re.compile(
    r"(ORD-\d+) \| (user_\d+) \| ([^|]+?) \| (" + _DATE + r") \| ([A-Z]{3} [\d.,]+) \| "
    r"Retain until " + _DATE
)
_NAME_LINE: Final = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z'\-]+){1,3}$")
_LABELS: Final = frozenset({"Detail", "Record", "Item"})


def extract_text(path: Path) -> str:
    import pymupdf  # lazy: only needed when PDFs are ingested

    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


def is_text_usable(text: str) -> bool:
    """Enough text to hold at least one identifier, resource id or register row."""
    if len(re.sub(r"\s+", "", text)) < 40:
        return False
    return bool(
        _EMAIL.search(text) or _USER.search(text) or _RESOURCE.search(text) or _ORD_ROW.search(text)
    )


def _iso(day: str, month: str, year: str, hour: str | None, minute: str | None) -> str:
    dt = datetime(
        int(year), _MONTHS[month], int(day), int(hour or 0), int(minute or 0), tzinfo=timezone.utc
    )
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _labelled_date(label: str, text: str) -> str | None:
    m = re.search(rf"{label}\s+{_DATE}", text, re.I)
    return _iso(*m.groups()) if m else None


def _person_name(text: str) -> bool:
    """A name line: 'Customer First Last', or a capitalised line right before an email."""
    if re.search(r"^Customer [A-Z][a-z]+(?: [A-Z][a-z'\-]+)+$", text, re.M):
        return True
    lines = [l.strip() for l in text.splitlines()]
    for i, line in enumerate(lines):
        if _EMAIL.search(line) and i > 0:
            prev = next((l for l in reversed(lines[:i]) if l not in _LABELS), "")
            if _NAME_LINE.match(prev):
                return True
    return False


class PdfAdapter:
    def __init__(self, perception: PerceptionAgent, mode: RunMode, report: IngestReport) -> None:
        self._perception = perception
        self._mode = mode
        self._report = report

    def parse(self, path: Path, row: ManifestRow) -> list[Fact]:
        text = extract_text(path)
        if not is_text_usable(text):
            reason = "PDF text layer unusable; perception fallback not implemented yet"
            self._report.skipped.append(SkippedEvidence(row.file_id, row.captured_at, reason))
            self._report.warnings.append(f"{row.file_id}: {reason}")
            return []
        em = Emitter(row, self._report, src="document")
        for label in ("captured", "issued", "generated"):
            em.check_capture(_labelled_date(label, text), f"'{label}' stamp")
        title = next((l.strip() for l in text.splitlines() if l.strip()), "pdf")
        base: dict[str, object] = {
            "title": title,
            "recorded_at": _labelled_date("recorded", text),
            "submitted_at": _labelled_date("submitted", text),
        }

        rows = list(_ORD_ROW.finditer(text))
        if rows:
            for m in rows:
                g = m.groups()
                retain = _iso(*g[-5:])
                self._emit(
                    em,
                    resource_id=None,
                    emails=(),
                    phones=(),
                    users=(g[1],),
                    name=False,
                    table=title,
                    extra={**base, "order_reference": g[0], "reason": g[2], "retain_until": retain},
                )
            return em.facts

        emails = tuple(dict.fromkeys(_EMAIL.findall(text)))
        phones = tuple(dict.fromkeys(p for p in _PHONE.findall(text)))
        users = tuple(dict.fromkeys(_USER.findall(text)))
        resources = tuple(dict.fromkeys(r for r in _RESOURCE.findall(text) if not _USER.fullmatch(r)))
        extra = {**base, "retain_until": _labelled_date("retain until", text)}
        for rid in resources or (None,):
            self._emit(
                em, resource_id=rid, emails=emails, phones=phones, users=users,
                name=_person_name(text), table=title, extra=extra,
            )
        return em.facts

    @staticmethod
    def _emit(
        em: Emitter,
        *,
        resource_id: str | None,
        emails: tuple[str, ...],
        phones: tuple[str, ...],
        users: tuple[str, ...],
        name: bool,
        table: str,
        extra: dict[str, object],
    ) -> None:
        idents = {normalize_value("email", e) for e in emails}
        idents |= {normalize_value("phone", p) for p in phones}
        idents |= {normalize_value("subject_reference", u) for u in users}
        idents.discard(None)
        pii = (["email"] if emails else []) + (["phone"] if phones else []) + (["name"] if name else [])
        em.emit(
            FactKind.PRESENCE,
            em.row.captured_at,
            system_id=em.row.origin,
            resource_id=resource_id,
            identifiers=frozenset(i for i in idents if i is not None),
            table=table,
            attrs={
                **extra,
                ATTR_STATE: "FOUND",
                ATTR_PII_FIELDS: ",".join(pii) or None,
                ATTR_ID_FIELDS: ",".join(
                    (["email"] if emails else []) + (["phone"] if phones else []) + (["user"] if users else [])
                )
                or None,
            },
        )
