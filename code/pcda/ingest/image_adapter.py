"""Screenshot evidence (PNG), read only through the perception agent.

The adapter asks the perception agent for a transcription in every mode; the
LLM client serves its disk cache first, so deterministic and replay runs reuse
the recorded extraction with no provider call. Without a cached answer in
deterministic mode the file yields no facts, is recorded as SkippedEvidence,
and every case whose audit window includes it is flagged.

Grounding is done here, in code, never by the model:
  * a record whose resource_id does not match a known resource prefix
    (from system_activity and the text evidence) is dropped;
  * identifiers are normalized exactly like text evidence;
  * observed_at is the latest visible timestamp at or before the manifest
    captured_at, else captured_at;
  * state words become a fact kind only through fixed vocabularies;
  * facts carry src=perception, which ranks below db_row for attribution.
Images are ingested after every text file, so cross_check can compare each
image record with text facts about the same resource and flag disagreements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from pcda.agents.perception import PerceivedRecord, PerceivedScreen, PerceptionAgent
from pcda.facts import ATTR_PII_FIELDS, ATTR_STATE, Fact, FactKind, FactStore, parse_ts
from pcda.ingest.base import IngestReport, ManifestRow, SkippedEvidence
from pcda.ingest.common import Emitter, ident_class, normalize_value
from pcda.llm.client import CacheMiss, LLMUnavailable, RunMode

SRC_PERCEPTION: Final = "perception"

_RESOURCE_ID: Final = re.compile(r"^([a-z]+(?:_[a-z]+)*_)(\d+)$")

# State vocabularies, matched as whole words on the lower-cased state text.
_ABSENT_WORDS: Final = ("deleted", "not found", "removed", "purged", "no record", "does not exist")
_CLEARED_WORDS: Final = ("redacted", "anonymized", "anonymised", "masked")
# "Continuous" is the BridgeOps connector mode column (evidence_14); the detail
# panel spells the same mode "Continuous sync".
_FLOW_ACTIVE: Final = ("enabled", "active", "running", "continuous sync", "continuous", "syncing", "connected")
_FLOW_OFF: Final = ("disabled", "paused", "stopped", "disconnected", "inactive")
_RESTORE_ACTIVE: Final = ("restore allowed", "restorable", "restore available", "restore enabled")
_RESTORE_OFF: Final = ("restore blocked", "blocked", "restore disabled", "restore not allowed", "legal hold")


def _has(text: str, words: tuple[str, ...]) -> str | None:
    for w in words:
        if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text):
            return w
    return None


def _kind(prefix: str, states: tuple[str, ...]) -> tuple[FactKind, str, str | None]:
    """(kind, raw state, matched word). Order: absence, flow/restore state, cleared, presence."""
    text = " | ".join(s.lower() for s in states)
    if (w := _has(text, _ABSENT_WORDS)) and not _has(text, ("not deleted",)):
        return FactKind.ABSENCE, "NOT_FOUND", w
    if prefix == "data_flow_":
        if w := _has(text, _FLOW_OFF):
            return FactKind.FLOW_STATE, "DISABLED", w
        if w := _has(text, _FLOW_ACTIVE):
            return FactKind.FLOW_STATE, "ACTIVE", w
    if prefix == "backup_":
        if w := _has(text, _RESTORE_OFF):
            return FactKind.RESTORE_STATE, "BLOCKED", w
        if w := _has(text, _RESTORE_ACTIVE):
            return FactKind.RESTORE_STATE, "ACTIVE", w
    if w := _has(text, _CLEARED_WORDS):
        return FactKind.PRESENCE, "FOUND", w  # pii_fields decide cleared vs exposed
    return FactKind.PRESENCE, "FOUND", None


def _observed_at(record: PerceivedRecord, screen: PerceivedScreen, captured_at: datetime) -> tuple[datetime, str]:
    candidates: list[datetime] = []
    for text, iso in record.timestamps:
        if not iso:
            continue
        try:
            ts = parse_ts(iso)
        except ValueError:
            continue
        if str(ts.year) in text and ts <= captured_at:  # the year must be on screen, not supplied
            candidates.append(ts)
    if candidates:
        return max(candidates), "visible"
    return captured_at, "captured_at"


@dataclass
class ImageAdapter:
    perception: PerceptionAgent
    mode: RunMode
    report: IngestReport
    known_prefixes: frozenset[str] = frozenset()
    text_store: FactStore | None = None
    known_systems: frozenset[str] = frozenset()
    screens: dict[str, PerceivedScreen] = field(default_factory=dict)

    def bind_text_store(self, store: FactStore, extra_prefixes: frozenset[str] = frozenset()) -> None:
        """Called by ingest_all after every text file: prefixes and systems seen in text evidence."""
        self.text_store = store
        prefixes = {m.group(1) for f in store if f.resource_id and (m := _RESOURCE_ID.match(f.resource_id))}
        self.known_prefixes = frozenset(prefixes) | self.known_prefixes | extra_prefixes
        self.known_systems = frozenset(f.system_id for f in store if f.system_id)

    def parse(self, path: Path, row: ManifestRow) -> list[Fact]:
        try:
            if self.perception is None:
                raise LLMUnavailable("no perception agent configured")
            screen = self.perception.extract_image(path, row)
        except (LLMUnavailable, CacheMiss, NotImplementedError, RuntimeError) as exc:
            reason = f"no perception result ({type(exc).__name__}: {exc})"
            self.report.skipped.append(SkippedEvidence(row.file_id, row.captured_at, reason))
            self.report.warnings.append(f"{row.file_id}: image skipped, no facts emitted: {reason}")
            return []
        self.screens[row.file_id] = screen
        return ground_screen(screen, row, self.report, self.known_prefixes, self.known_systems)

    def cross_check(self, image_facts: list[Fact]) -> None:
        """Flag image records whose identifiers disagree with text facts about the same resource."""
        if self.text_store is None:
            return
        for f in image_facts:
            if f.kind is FactKind.LINK or f.resource_id is None:
                continue
            text_facts = [t for t in self.text_store.by_resource(f.resource_id) if t.attrs.get("src") != SRC_PERCEPTION]
            if not text_facts:
                continue
            for cls in ("email", "phone", "hash", "ref"):
                mine = {i for i in f.identifiers if ident_class(i) == cls}
                theirs = {i for t in text_facts for i in t.identifiers if ident_class(i) == cls}
                if mine and theirs and not (mine & theirs):
                    self.report.conflicts.append(
                        f"{f.evidence_id}: {f.resource_id} {cls} on screen {sorted(mine)} "
                        f"!= text evidence {sorted(theirs)} ({sorted({t.evidence_id for t in text_facts})})"
                    )


def ground_screen(
    screen: PerceivedScreen,
    row: ManifestRow,
    report: IngestReport,
    known_prefixes: frozenset[str],
    known_systems: frozenset[str] = frozenset(),
) -> list[Fact]:
    """Turn a transcription into Facts; drop records that fail grounding."""
    em = Emitter(row, report, src=SRC_PERCEPTION)
    for n, rec in enumerate(screen.records, start=1):
        rid = (rec.resource_id or "").strip()
        m = _RESOURCE_ID.match(rid)
        if not m or m.group(1) not in known_prefixes:
            # The model sometimes transcribes a visible resource id into the wrong slot
            # (marketing_001 among identifiers, support_1034 as a field). Adopt it only
            # when exactly one value of this record matches a known id pattern.
            transcribed = [*rec.emails, *rec.phones, *rec.user_ids, *rec.hashes, *rec.fields.values()]
            found = sorted({
                v.strip() for v in transcribed
                if (mv := _RESOURCE_ID.match(v.strip())) and mv.group(1) in known_prefixes
            })
            if len(found) == 1:
                report.warnings.append(f"{row.file_id}: record {n} resource_id {rid or None!r} -> {found[0]} (only id transcribed)")
                rid = found[0]
                m = _RESOURCE_ID.match(rid)
        if not m or m.group(1) not in known_prefixes:
            report.warnings.append(f"{row.file_id}: record {n} dropped, resource_id {rid or None!r} is not a known pattern")
            continue
        prefix = m.group(1)
        identifiers: set[str] = set()
        pii: list[str] = []
        for key, values in (("email", rec.emails), ("phone", rec.phones), ("user_id", rec.user_ids),
                            ("email_hash", rec.hashes)):
            for v in values:
                ident = normalize_value(key, v)
                if ident is None:
                    report.warnings.append(f"{row.file_id}: {rid} {key} {v!r} does not normalize; dropped")
                    continue
                identifiers.add(ident)
                if ident_class(ident) != "ref" and key not in pii:
                    pii.append(key)
        # Identifiers shown only as field values (e.g. "Customer email: ...").
        for label, value in rec.fields.items():
            key = label.lower().replace(" ", "_")
            ident = normalize_value(key, value)
            if ident is not None:
                identifiers.add(ident)
                if ident_class(ident) != "ref" and key not in pii:
                    pii.append(key)
        kind, state, word = _kind(prefix, rec.states)
        observed_at, when = _observed_at(rec, screen, row.captured_at)
        system = rec.system if rec.system in known_systems else None
        em.emit(
            kind,
            observed_at,
            system_id=system,
            resource_id=rid,
            identifiers=frozenset(identifiers),
            table=f"screen:{screen.screen or 'unknown'}",
            source_line=f"record {n}",
            attrs={
                ATTR_STATE: state,
                ATTR_PII_FIELDS: ",".join(pii) if pii and kind is not FactKind.ABSENCE else None,
                "state_word": word,
                "screen_system": rec.system,
                "observed_from": when,
                "states": " | ".join(rec.states) or None,
            },
        )
    return em.facts
