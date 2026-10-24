"""Obligations and Certificates: the proof object for one case.

An Obligation says a resource must reach a required state. It is DISCHARGED
only by a Fact that proves that state at audit_at, OPEN when the latest
fact contradicts it, and UNKNOWN when nothing proves either way. The verdict
follows from the obligation set; steps_json is a projection of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pcda.agents.intake import IntakeResult
from pcda.cases import CaseRequest
from pcda.facts import Fact, FactStore
from pcda.identity import AttributedResource
from pcda.lineage import LineageGraph
from pcda.policy import ObligationTemplate


class ObligationStatus(str, Enum):
    DISCHARGED = "DISCHARGED"
    OPEN = "OPEN"
    UNKNOWN = "UNKNOWN"


class Verdict(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class Obligation:
    resource: AttributedResource
    category: str
    template: ObligationTemplate
    status: ObligationStatus
    discharged_by: Fact | None = None  # proving fact when DISCHARGED
    violated_by: Fact | None = None  # latest contradicting fact when OPEN

    @property
    def system_id(self) -> str:
        return self.resource.system_id

    @property
    def resource_id(self) -> str:
        return self.resource.resource_id

    @property
    def required_state(self) -> str:
        return self.template.required_state


@dataclass(frozen=True)
class Certificate:
    case_id: str
    subject: str | None  # user_id, None when unresolved
    obligations: tuple[Obligation, ...]
    verdict: Verdict
    flags: tuple[str, ...] = field(default_factory=tuple)  # e.g. "perception_skipped:evidence_12"


def solve(
    case: CaseRequest,
    intake: IntakeResult,
    subject: str | None,
    attributions: list[AttributedResource],
    store: FactStore,
    templates: dict[str, ObligationTemplate],
    lineage: LineageGraph,
) -> Certificate:
    """Build obligations for every attributed resource and active flow/restore path,
    discharge them from `store` (already as_of audit_at), and derive the verdict."""
    raise NotImplementedError("solver.solve")
