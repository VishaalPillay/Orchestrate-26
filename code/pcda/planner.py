"""Project a Certificate onto steps_json.

Ordering is contract.canonical_sort_key only. Citation rule:
  * a resource that some fact mentions cites the latest such fact's
    evidence_id at or before audit_at;
  * an ABSENT resource no fact mentions cites the latest coverage snapshot of
    its table at or before audit_at (mention wins over coverage, cases 21, 25-28).
Verification set:
  * INCOMPLETE: remediated resources + the account + every RETAIN obligation
    (each with its template's verify operation), evidence_id "".
  * COMPLETE: every obligation, each citing its proving evidence.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime

from pcda.contract import STEP_FIELDS, canonical_sort_key
from pcda.facts import FactStore
from pcda.solver import Certificate, Obligation


@dataclass(frozen=True)
class Step:
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str
    step_no: int = 0

    def sort_key(self) -> tuple[int, str, str]:
        return canonical_sort_key(self.operation, self.system_id, self.resource_id)


def order_steps(steps: list[Step]) -> list[Step]:
    """Canonical order, numbered from 1."""
    ordered = sorted(steps, key=Step.sort_key)
    return [
        Step(s.phase, s.system_id, s.resource_id, s.operation, s.state, s.evidence_id, i)
        for i, s in enumerate(ordered, start=1)
    ]


def serialize_steps(steps: list[Step]) -> str:
    """Deterministic JSON: fields in STEP_FIELDS order, compact separators."""
    payload = [{k: asdict(s)[k] for k in STEP_FIELDS} for s in steps]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def cite(obligation: Obligation, store: FactStore, audit_at: datetime) -> str:
    raise NotImplementedError("planner.cite")


def project(certificate: Certificate, store: FactStore, audit_at: datetime) -> list[Step]:
    raise NotImplementedError("planner.project")
