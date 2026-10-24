"""Facts: the only currency between evidence and decisions.

Adapters emit Facts; nothing downstream reads raw evidence. Identifiers on a
Fact are already normalized (see identity.normalize_identifier).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Iterator, Mapping


class FactKind(str, Enum):
    PRESENCE = "PRESENCE"
    ABSENCE = "ABSENCE"
    PII_EXPOSED = "PII_EXPOSED"
    PII_CLEARED = "PII_CLEARED"
    FLOW_STATE = "FLOW_STATE"
    RESTORE_STATE = "RESTORE_STATE"
    LINK = "LINK"


# attrs keys with fixed meaning across adapters.
ATTR_TABLE = "table"  # table or collection a fact was read from
ATTR_COVERAGE = "coverage"  # "true" on a table-level ABSENCE fact (resource_id is None)
ATTR_VACUOUS = "is_vacuous"  # "true" when the whole snapshot holds no data rows
ATTR_SOURCE_LINE = "source_line"  # 1-based line or row reference inside the file
ATTR_STATE = "state"  # raw source state: FOUND, NOT_FOUND, IDENTIFIABLE, ACTIVE, DISABLED, BLOCKED, ...
ATTR_PII_FIELDS = "pii_fields"  # comma list of direct-identifier fields still populated on the record
ATTR_ID_FIELDS = "id_fields"  # comma list of every field that contributed an identifier
ATTR_LINKED = "linked_resource"  # LINK facts: the resource this one was derived from


def parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (with 'Z' or offset) into an aware UTC datetime."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True, eq=False)
class Fact:
    fact_id: str
    evidence_id: str
    captured_at: datetime
    observed_at: datetime
    system_id: str | None
    resource_id: str | None
    category: str | None
    kind: FactKind
    identifiers: frozenset[str] = frozenset()
    attrs: Mapping[str, str] = field(default_factory=dict)
    topology: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attrs, MappingProxyType):
            object.__setattr__(self, "attrs", MappingProxyType(dict(self.attrs)))

    def __hash__(self) -> int:
        return hash(self.fact_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Fact):
            return NotImplemented
        return self.fact_id == other.fact_id

    @property
    def is_coverage(self) -> bool:
        return self.kind is FactKind.ABSENCE and self.attrs.get(ATTR_COVERAGE) == "true"


class FactStore:
    """Facts with inverted indexes by normalized identifier and by resource_id."""

    def __init__(self, facts: Iterable[Fact] = ()) -> None:
        self._facts: dict[str, Fact] = {}
        self._by_identifier: dict[str, set[str]] = defaultdict(set)
        self._by_resource: dict[str, set[str]] = defaultdict(set)
        for f in facts:
            self.add(f)

    def add(self, fact: Fact) -> None:
        if fact.fact_id in self._facts:
            raise ValueError(f"duplicate fact_id {fact.fact_id}")
        self._facts[fact.fact_id] = fact
        for ident in fact.identifiers:
            self._by_identifier[ident].add(fact.fact_id)
        if fact.resource_id is not None:
            self._by_resource[fact.resource_id].add(fact.fact_id)

    def __len__(self) -> int:
        return len(self._facts)

    def __iter__(self) -> Iterator[Fact]:
        return iter(self._facts.values())

    def by_identifier(self, identifier: str) -> list[Fact]:
        return self._sorted(self._by_identifier.get(identifier, ()))

    def by_resource(self, resource_id: str) -> list[Fact]:
        return self._sorted(self._by_resource.get(resource_id, ()))

    def coverage(self) -> list[Fact]:
        return [f for f in self._facts.values() if f.is_coverage]

    def as_of(self, t: datetime) -> FactStore:
        """Facts knowable at t. Both bounds are mandatory and inclusive.

        A file captured before t can still carry records observed after t
        (evidence_13, evidence_36; cases 135 and 383), so observed_at is
        checked independently of captured_at.
        """
        return FactStore(f for f in self._facts.values() if f.captured_at <= t and f.observed_at <= t)

    def _sorted(self, ids: Iterable[str]) -> list[Fact]:
        facts = [self._facts[i] for i in ids]
        facts.sort(key=lambda f: (f.observed_at, f.captured_at, f.evidence_id, f.fact_id))
        return facts
