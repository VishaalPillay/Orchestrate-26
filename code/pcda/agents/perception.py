"""Perception agent: reads screenshots (and unusable PDF text layers).

It returns raw transcriptions only. Code in the adapters turns a transcribed
record into a Fact after grounding it: the resource_id must match a known
resource prefix, identifiers are normalized, and timestamps are clamped to the
file's capture time (see ingest/image_adapter.py). The model never decides a
category, a state or an owner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

from pcda.agents import prompts
from pcda.ingest.base import ManifestRow
from pcda.llm.client import Attachment, LLMClient, LLMRequest, LLMUnavailable

PERCEPTION_TASK: Final = "perception.image"
PERCEPTION_MODEL_ENV: Final = "PERCEPTION_MODEL"

# Top-level shape the answer must have (checked by the client's strict JSON pass).
PERCEPTION_SCHEMA: Final[dict[str, Any]] = {"type": "object", "required": ["records"]}


@dataclass(frozen=True)
class RawObservation:
    evidence_id: str
    system_id: str | None
    resource_id: str | None
    field: str  # e.g. "email", "phone", "status", "restore_allowed", "timestamp"
    value: str
    quote: str  # on-screen or in-text span the value was read from


@dataclass(frozen=True)
class PerceivedRecord:
    """One record as the model transcribed it. Untrusted until grounded."""

    resource_id: str | None
    system: str | None
    fields: dict[str, str] = field(default_factory=dict)
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    user_ids: tuple[str, ...] = ()
    hashes: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    timestamps: tuple[tuple[str, str | None], ...] = ()  # (verbatim, iso or None)


@dataclass(frozen=True)
class PerceivedScreen:
    evidence_id: str
    screen: str | None
    records: tuple[PerceivedRecord, ...]
    visible_timestamps: tuple[str, ...] = ()
    provider: str = ""
    model: str = ""
    cached: bool = False


class PerceptionAgent(Protocol):
    def extract_image(self, path: Path, row: ManifestRow) -> PerceivedScreen: ...

    def extract_text(self, text: str, row: ManifestRow) -> list[RawObservation]: ...


def _strs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(v).strip() for v in value if isinstance(v, (str, int)) and str(v).strip())


def parse_screen(evidence_id: str, data: Any) -> tuple[PerceivedScreen, list[str]]:
    """Defensive parse of the model's JSON; malformed records are dropped with a note."""
    notes: list[str] = []
    records: list[PerceivedRecord] = []
    raw_records = data.get("records") if isinstance(data, dict) else None
    for i, r in enumerate(raw_records if isinstance(raw_records, list) else []):
        if not isinstance(r, dict):
            notes.append(f"record {i}: not an object")
            continue
        raw_idents = r.get("identifiers")
        idents: dict[str, Any] = raw_idents if isinstance(raw_idents, dict) else {}
        raw_fields = r.get("fields")
        fields: dict[str, Any] = raw_fields if isinstance(raw_fields, dict) else {}
        stamps: list[tuple[str, str | None]] = []
        for t in r.get("timestamps") or []:
            if isinstance(t, dict) and t.get("text"):
                iso = t.get("iso")
                stamps.append((str(t["text"]), str(iso) if iso else None))
            elif isinstance(t, str):
                stamps.append((t, None))
        rid = r.get("resource_id")
        records.append(
            PerceivedRecord(
                resource_id=str(rid).strip() if rid else None,
                system=str(r["system"]).strip() if r.get("system") else None,
                fields={str(k): str(v) for k, v in fields.items() if v is not None and str(v).strip()},
                emails=_strs(idents.get("emails")),
                phones=_strs(idents.get("phones")),
                user_ids=_strs(idents.get("user_ids")),
                hashes=_strs(idents.get("hashes")),
                states=_strs(r.get("states")),
                timestamps=tuple(stamps),
            )
        )
    screen = data.get("screen") if isinstance(data, dict) else None
    return (
        PerceivedScreen(
            evidence_id,
            str(screen) if screen else None,
            tuple(records),
            _strs(data.get("visible_timestamps")) if isinstance(data, dict) else (),
        ),
        notes,
    )


class LLMPerceptionAgent:
    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def extract_image(self, path: Path, row: ManifestRow) -> PerceivedScreen:
        """One call per image. The client serves the cache first in every mode."""
        if self._client is None:
            raise LLMUnavailable(f"{row.file_id}: no LLM client")
        system, user = prompts.load(PERCEPTION_TASK)
        request = LLMRequest(
            task=PERCEPTION_TASK,
            prompt=user,
            system=system,
            model_env=PERCEPTION_MODEL_ENV,
            schema=PERCEPTION_SCHEMA,
            attachments=(Attachment("image/png", path.read_bytes()),),
        )
        resp = self._client.complete(request)
        screen, _ = parse_screen(row.file_id, resp.parsed)
        return PerceivedScreen(
            screen.evidence_id, screen.screen, screen.records, screen.visible_timestamps,
            resp.provider, resp.model, resp.cached,
        )

    def extract_text(self, text: str, row: ManifestRow) -> list[RawObservation]:
        raise NotImplementedError("perception.LLMPerceptionAgent.extract_text")
