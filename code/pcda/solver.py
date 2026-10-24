"""Obligations and Certificates: the proof object for one case.

An Obligation says a resource must reach a required state. It is DISCHARGED
only by a Fact that proves that state at audit_at, OPEN when the latest
fact contradicts it, and UNKNOWN when nothing proves either way. The verdict
follows from the obligation set; steps_json is a projection of it.

State of a resource at audit_at (`store` is already as_of(audit_at)):
  * the facts about the resource (LINK and table coverage excluded) observed
    last decide; among facts observed at that same instant the conservative
    reading wins, so a row present in any replica topology is present;
  * a stored record is EXPOSED when its pii_fields are populated, CLEARED
    otherwise (this only matters for REDACT / ANONYMIZE / tombstone rules);
  * a resource with no fact at all is ABSENT only if a non-vacuous table
    snapshot covering its system exists (DELETE rules only).

Verdict: INCOMPLETE iff some obligation is OPEN. An UNKNOWN obligation cannot
yield a cited finding, so on its own it does not make a case INCOMPLETE; it
is flagged and left out of a COMPLETE case's steps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from pcda.agents.intake import IntakeResult
from pcda.cases import CaseRequest
from pcda.category import CategoryResolver
from pcda.facts import ATTR_PII_FIELDS, ATTR_STATE, ATTR_VACUOUS, Fact, FactKind, FactStore
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


# Readings of a resource at audit_at.
ABSENT: Final = "ABSENT"
PRESENT: Final = "PRESENT"
EXPOSED: Final = "EXPOSED"  # record present, direct identifiers still populated
CLEARED: Final = "CLEARED"  # record present, no direct identifier populated
ACTIVE: Final = "ACTIVE"
DISABLED: Final = "DISABLED"
BLOCKED: Final = "BLOCKED"

# Tie-break among facts observed at the same instant: higher wins.
_CONSERVATIVE: Final[dict[str, int]] = {
    EXPOSED: 6, PRESENT: 5, ACTIVE: 4, CLEARED: 3, DISABLED: 2, BLOCKED: 1, ABSENT: 0,
}
_PII_STATES: Final[frozenset[str]] = frozenset({"REDACTED", "ANONYMIZED"})
_GUARD_STATES: Final[frozenset[str]] = frozenset({"DISABLED", "BLOCKED"})


@dataclass(frozen=True)
class Obligation:
    resource: AttributedResource
    category: str
    template: ObligationTemplate
    status: ObligationStatus
    discharged_by: Fact | None = None  # proving fact when DISCHARGED
    violated_by: Fact | None = None  # latest contradicting fact when OPEN
    observed_state: str | None = None  # reading at audit_at (ABSENT, EXPOSED, ACTIVE, ...)
    via_coverage: bool = False  # ABSENT read from a table snapshot, not a resource fact

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

    def with_flags(self, extra: tuple[str, ...]) -> Certificate:
        return Certificate(self.case_id, self.subject, self.obligations, self.verdict, self.flags + extra)


def _reading(fact: Fact, template: ObligationTemplate) -> str | None:
    """What one fact says about the resource, in terms the template cares about."""
    pii_rule = template.required_state in _PII_STATES
    guard_rule = template.required_state in _GUARD_STATES
    if fact.kind is FactKind.ABSENCE:
        return ABSENT
    if fact.kind in (FactKind.FLOW_STATE, FactKind.RESTORE_STATE):
        state = fact.attrs.get(ATTR_STATE)
        if guard_rule:
            return state if state in (ACTIVE, DISABLED, BLOCKED) else None
        return PRESENT
    if guard_rule:
        return None  # a plain row says nothing about whether a flow runs or a restore is allowed
    if fact.kind is FactKind.PII_EXPOSED:
        return EXPOSED if pii_rule else PRESENT
    if fact.kind is FactKind.PII_CLEARED:
        return CLEARED if pii_rule else PRESENT
    if fact.kind is FactKind.PRESENCE:
        if pii_rule:
            return EXPOSED if fact.attrs.get(ATTR_PII_FIELDS) else CLEARED
        return PRESENT
    return None


def resource_state(
    resource: AttributedResource, template: ObligationTemplate, store: FactStore
) -> tuple[str | None, Fact | None, bool]:
    """(reading, deciding fact, via_coverage) at audit_at."""
    late = set(resource.late_evidence_ids)
    readings: list[tuple[Fact, str]] = []
    for f in store.by_resource(resource.resource_id):
        if f.kind is FactKind.LINK or f.is_coverage or f.evidence_id in late:
            continue
        r = _reading(f, template)
        if r is not None:
            readings.append((f, r))
    if readings:
        latest = max(f.observed_at for f, _ in readings)
        tied = [(f, r) for f, r in readings if f.observed_at == latest]
        tied.sort(key=lambda fr: (_CONSERVATIVE[fr[1]], fr[0].captured_at, fr[0].evidence_id, fr[0].fact_id))
        fact, reading = tied[-1]
        return reading, fact, False
    if template.required_state == ABSENT:
        snap = covering_snapshot(resource.system_id, store)
        if snap is not None:
            return ABSENT, snap, True
    return None, None, False


def covering_snapshot(system_id: str, store: FactStore) -> Fact | None:
    """Latest non-vacuous table snapshot that covers system_id (store is as_of audit_at)."""
    best: Fact | None = None
    for f in store.coverage():
        if f.attrs.get(ATTR_VACUOUS) == "true":
            continue
        systems = {s for s in f.attrs.get("systems", "").split(",") if s}
        if f.system_id != system_id and system_id not in systems:
            continue
        if best is None or (f.captured_at, f.observed_at, f.evidence_id) > (
            best.captured_at,
            best.observed_at,
            best.evidence_id,
        ):
            best = f
    return best


def _judge(required: str, reading: str | None) -> ObligationStatus:
    if reading is None:
        return ObligationStatus.UNKNOWN
    if required == ABSENT:
        return ObligationStatus.DISCHARGED if reading == ABSENT else ObligationStatus.OPEN
    if required in _PII_STATES:
        if reading in (CLEARED, ABSENT):
            return ObligationStatus.DISCHARGED
        return ObligationStatus.OPEN if reading == EXPOSED else ObligationStatus.UNKNOWN
    if required == PRESENT:
        return ObligationStatus.UNKNOWN if reading == ABSENT else ObligationStatus.DISCHARGED
    # DISABLED / BLOCKED: an absent flow or archive can no longer copy or restore.
    if reading in (required, ABSENT):
        return ObligationStatus.DISCHARGED
    return ObligationStatus.OPEN if reading == ACTIVE else ObligationStatus.UNKNOWN


def solve(
    case: CaseRequest,
    intake: IntakeResult,
    subject: str | None,
    attributions: list[AttributedResource],
    store: FactStore,
    templates: dict[str, ObligationTemplate],
    lineage: LineageGraph,
    *,
    full_store: FactStore,
    resolver: CategoryResolver,
) -> Certificate:
    """Build obligations for every attributed resource and active flow/restore path,
    discharge them from `store` (already as_of audit_at), and derive the verdict.

    `full_store` is only used to resolve categories, which are time-invariant.
    """
    flags: list[str] = []
    if intake.authorizes_deletion is False:
        return Certificate(case.case_id, subject, (), Verdict.NOT_APPLICABLE, (f"intake:{intake.request_kind}",))
    if intake.is_ambiguous:
        flags.append("intake_ambiguous")

    obligations: list[Obligation] = []
    for res in attributions:
        cat = resolver.resolve_resource(
            full_store.by_resource(res.resource_id), lineage.claims_for(res.resource_id, case.audit_at)
        )
        if cat.unknown_names:
            flags.append(f"category_unknown_name:{res.resource_id}:{'|'.join(cat.unknown_names)}")
        if cat.conflicts:
            flags.append(f"category_conflict:{res.resource_id}:{cat.category} vs {'|'.join(cat.conflicts)}")
        if cat.category is None:
            flags.append(f"category_unknown:{res.resource_id}")
            continue
        template = templates[cat.category]
        reading, fact, via_coverage = resource_state(res, template, store)
        status = _judge(template.required_state, reading)
        if template.required_state == PRESENT and reading == ABSENT:
            flags.append(f"retained_missing:{res.resource_id}")
        if status is ObligationStatus.UNKNOWN:
            flags.append(f"unknown_state:{res.resource_id}")
        obligations.append(
            Obligation(
                resource=res,
                category=cat.category,
                template=template,
                status=status,
                discharged_by=fact if status is ObligationStatus.DISCHARGED else None,
                violated_by=fact if status is ObligationStatus.OPEN else None,
                observed_state=reading,
                via_coverage=via_coverage,
            )
        )
    obligations.sort(key=lambda o: (o.system_id, o.resource_id))

    if any(o.status is ObligationStatus.OPEN for o in obligations):
        verdict = Verdict.INCOMPLETE
    elif any(o.status is ObligationStatus.DISCHARGED for o in obligations):
        verdict = Verdict.COMPLETE
    else:
        # Nothing provable either way: no contract-valid COMPLETE or INCOMPLETE row exists.
        verdict = Verdict.NOT_APPLICABLE
        flags.append("fallback_no_provable_obligation")
    return Certificate(case.case_id, subject, tuple(obligations), verdict, tuple(flags))
