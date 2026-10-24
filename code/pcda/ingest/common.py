"""Helpers shared by every adapter.

  * Emitter: builds Facts for one evidence file with deterministic fact_ids and
    the manifest captured_at (authoritative).
  * extract_identifiers / normalize_value: identifiers are normalized at ingest
    (lowercase emails, digit-only phones) so the FactStore index is exact.
  * result_to_kind: the shared vocabulary of ledger-style `result` / `state`
    values (FOUND, NOT_FOUND, IDENTIFIABLE, ...).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Final, Iterable, Mapping

from dataclasses import dataclass, field

from pcda.facts import (
    ATTR_ID_FIELDS,
    ATTR_LINKED,
    ATTR_PII_FIELDS,
    ATTR_SOURCE_LINE,
    ATTR_STATE,
    ATTR_TABLE,
    Fact,
    FactKind,
    parse_ts,
)
from pcda.identity import normalize_identifier, normalize_phone
from pcda.ingest.base import IngestReport, ManifestRow

NULLS: Final = frozenset({"", "null", "none", "nan", "n/a", "-"})

# Ledger-style outcome vocabulary -> (kind, state). `state` keeps the raw
# source word so the solver can tell IDENTIFIABLE from ANONYMIZED.
_RESULT_KIND: Final[dict[str, FactKind]] = {
    "FOUND": FactKind.PRESENCE,
    "NOT_FOUND": FactKind.ABSENCE,
    "IDENTIFIABLE": FactKind.PII_EXPOSED,
    "REDACTED": FactKind.PII_CLEARED,
    "ANONYMIZED": FactKind.PII_CLEARED,
}
_FLOW_STATES: Final = frozenset({"ACTIVE", "DISABLED"})
_RESTORE_STATES: Final = frozenset({"ACTIVE", "BLOCKED"})


def is_null(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in NULLS)


# Fields whose value is an identity even when it is only digits (a phone number).
IDENTITY_KEYS: Final = frozenset(
    {
        "matched_value", "lookup_key", "lookup_value", "match_key", "scan_key", "subject_key",
        "owner_key", "correlation_key", "subject_reference", "subject_selector", "query",
    }
)


def normalize_value(key: str, value: object) -> str | None:
    """Normalized identifier for a (field name, value), or None if it is not one.

    Bare digit strings are phones only when the field name says so; this keeps
    counters, ports and cursors out of the identifier index.
    """
    if is_null(value) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip().strip('"')
    if "phone" in key.lower():
        digits = normalize_phone(text)
        return digits if len(digits) >= 7 else None
    ident = normalize_identifier(text)
    if ident is not None and ident.isdigit() and key not in IDENTITY_KEYS:
        return None
    return ident


def extract_identifiers(fields: Mapping[str, object], skip: Iterable[str] = ()) -> frozenset[str]:
    skipped = set(skip)
    out: set[str] = set()
    for key, value in fields.items():
        if key in skipped:
            continue
        ident = normalize_value(key, value)
        if ident is not None:
            out.add(ident)
    return frozenset(out)


def result_to_kind(
    record_type: str | None, result: str | None
) -> tuple[FactKind, str] | None:
    """Map a ledger (record_type, result) pair to a fact kind and raw state."""
    if result is None:
        return None
    word = result.strip().upper()
    if record_type == "DATA_FLOW" and word in _FLOW_STATES:
        return FactKind.FLOW_STATE, word
    if record_type == "BACKUP_ARCHIVE" and word in _RESTORE_STATES:
        return FactKind.RESTORE_STATE, word
    kind = _RESULT_KIND.get(word)
    return (kind, word) if kind is not None else None


def to_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
    return None


class Emitter:
    """Facts for one evidence file. captured_at always comes from the manifest."""

    def __init__(
        self, row: ManifestRow, report: IngestReport | None = None, scope: str | None = None
    ) -> None:
        """`scope` namespaces fact_ids when one file is parsed by several emitters (SQL tables)."""
        self.row = row
        self.report = report
        self._scope = f"{scope}:" if scope else ""
        self.facts: list[Fact] = []

    def warn(self, message: str) -> None:
        if self.report is not None:
            self.report.warnings.append(f"{self.row.file_id}: {message}")

    def check_capture(self, claimed: datetime | str | None, label: str) -> None:
        """Log (never apply) an in-file capture time that disagrees with the manifest."""
        if claimed is None:
            return
        ts = parse_ts(claimed) if isinstance(claimed, str) else claimed
        if ts != self.row.captured_at:
            self.warn(
                f"{label} {ts.isoformat()} != manifest captured_at "
                f"{self.row.captured_at.isoformat()} (manifest wins)"
            )

    def emit(
        self,
        kind: FactKind,
        observed_at: datetime,
        *,
        system_id: str | None,
        resource_id: str | None,
        category: str | None = None,
        identifiers: frozenset[str] = frozenset(),
        table: str | None = None,
        topology: str | None = None,
        source_line: int | str | None = None,
        attrs: Mapping[str, object] | None = None,
        **extra: object,
    ) -> Fact:
        """`attrs` carries arbitrary source field names; `extra` is sugar for fixed keys."""
        merged: dict[str, str] = {}
        if table is not None:
            merged[ATTR_TABLE] = table
        if source_line is not None:
            merged[ATTR_SOURCE_LINE] = str(source_line)
        for key, value in {**(attrs or {}), **extra}.items():
            if value is None:
                continue
            merged[key] = str(value).lower() if isinstance(value, bool) else str(value)
        fact = Fact(
            fact_id=f"{self.row.file_id}:{self._scope}{len(self.facts) + 1:05d}",
            evidence_id=self.row.file_id,
            captured_at=self.row.captured_at,
            observed_at=observed_at,
            system_id=system_id,
            resource_id=resource_id,
            category=category,
            kind=kind,
            identifiers=identifiers,
            attrs=merged,
            topology=topology,
        )
        self.facts.append(fact)
        return fact


_RESOURCE_RE: Final = re.compile(r"[a-z_]+_\d+")


def resource_like(value: object) -> bool:
    return isinstance(value, str) and bool(_RESOURCE_RE.fullmatch(value))


# ---------------------------------------------------------------------------
# Generic records (SQL rows, bundle records, service-export records)
# ---------------------------------------------------------------------------

NAME_KEYS: Final = frozenset({"full_name", "name", "display_name", "requester_name"})
_SKIP_KEYS: Final = frozenset(
    {"metrics", "recent_activity", "included_sections", "hits", "resource_id", "observed_at", "recorded_at"}
)
_TEXT_KEYS: Final = frozenset({"message_text"})  # free text: mined for emails, never stored
_EMAIL_IN_TEXT: Final = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def ident_class(ident: str) -> str:
    if "@" in ident:
        return "email"
    if len(ident) == 64 and all(c in "0123456789abcdef" for c in ident):
        return "hash"
    if ident.isdigit():
        return "phone"
    return "ref"  # user_N: the stable internal reference, not a direct identifier


@dataclass(frozen=True)
class RecordView:
    identifiers: frozenset[str] = frozenset()
    pii_fields: tuple[str, ...] = ()
    id_fields: tuple[str, ...] = ()
    scalars: dict[str, str] = field(default_factory=dict)


def _flatten(fields: Mapping[str, object]) -> Iterable[tuple[str, object]]:
    for key, value in fields.items():
        if key in _SKIP_KEYS and key != "resource_id":
            continue
        if isinstance(value, Mapping):
            yield from _flatten(value)
        elif isinstance(value, (list, tuple)):
            continue
        else:
            yield key, value


def view_record(fields: Mapping[str, object]) -> RecordView:
    """Identifiers and PII exposure of one record; scalars keep the non-identifier fields."""
    idents: set[str] = set()
    pii: list[str] = []
    id_fields: list[str] = []
    scalars: dict[str, str] = {}
    for key, value in _flatten(fields):
        if key == "resource_id" or is_null(value):
            continue
        ident = normalize_value(key, value)
        if ident is not None:
            idents.add(ident)
            id_fields.append(key)
            if ident_class(ident) != "ref":
                pii.append(key)
            continue
        if key in NAME_KEYS:
            pii.append(key)
            continue
        if isinstance(value, str) and "@" in value:
            found = {m.lower() for m in _EMAIL_IN_TEXT.findall(value)}
            if found:
                idents |= found
                id_fields.append(key)
                pii.append(key)
        if key in _TEXT_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)):
            scalars[key] = str(value).lower() if isinstance(value, bool) else str(value)
    return RecordView(frozenset(idents), tuple(pii), tuple(id_fields), scalars)


def record_state(fields: Mapping[str, object]) -> tuple[FactKind, str | None]:
    """Kind a stored record represents: a flow, a restore path, or plain presence."""
    enabled = to_bool(fields.get("enabled"))
    if enabled is not None:
        return FactKind.FLOW_STATE, "ACTIVE" if enabled else "DISABLED"
    allowed = to_bool(fields.get("restore_allowed"))
    if allowed is not None:
        return FactKind.RESTORE_STATE, "ACTIVE" if allowed else "BLOCKED"
    return FactKind.PRESENCE, None


def emit_record(
    em: Emitter,
    observed_at: datetime,
    *,
    system_id: str | None,
    resource_id: str,
    table: str,
    fields: Mapping[str, object],
    topology: str | None = None,
    source_line: int | str | None = None,
    category: str | None = None,
    **extra: object,
) -> Fact:
    """One fact for a stored record, plus a LINK fact per `*_resource_id` pointer."""
    view = view_record(fields)
    kind, state = record_state(fields)
    attrs: dict[str, object] = dict(view.scalars)
    attrs.update(extra)
    attrs[ATTR_STATE] = state or "FOUND"
    attrs[ATTR_PII_FIELDS] = ",".join(view.pii_fields) if view.pii_fields else None
    attrs[ATTR_ID_FIELDS] = ",".join(view.id_fields) if view.id_fields else None
    fact = em.emit(
        kind,
        observed_at,
        system_id=system_id,
        resource_id=resource_id,
        category=category,
        identifiers=view.identifiers,
        table=table,
        topology=topology,
        source_line=source_line,
        attrs=attrs,
    )
    for key, value in fields.items():
        if key.endswith("_resource_id") and key != "resource_id" and resource_like(value):
            em.emit(
                FactKind.LINK,
                observed_at,
                system_id=system_id,
                resource_id=resource_id,
                identifiers=view.identifiers,
                table=table,
                topology=topology,
                source_line=source_line,
                **{ATTR_LINKED: value, "via": key},
            )
    return fact
