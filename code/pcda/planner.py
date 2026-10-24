"""Project a Certificate onto steps_json.

Ordering is contract.canonical_sort_key only. Citation rule:
  * a resource that some fact mentions cites the latest such fact's
    evidence_id at or before audit_at;
  * an ABSENT resource no fact mentions cites the latest non-vacuous coverage
    snapshot of its system at or before audit_at (mention wins over coverage,
    cases 21, 25-28);
  * evidence captured after audit_at (late_evidence_ids) is never cited.
Verification set:
  * INCOMPLETE: remediated resources + the account + every RETAIN obligation
    (each with its template's verify operation), evidence_id "".
  * COMPLETE: every obligation, each citing its proving evidence.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Final

from pcda.contract import OPERATIONS, STEP_FIELDS, canonical_sort_key
from pcda.facts import FactStore
from pcda.solver import ABSENT, Certificate, Obligation, ObligationStatus, Verdict

# Required state -> (finding, remediation) for an OPEN obligation.
_OPEN_STEPS: Final[dict[str, tuple[str, str]]] = {
    "ABSENT": ("REPORT_REMAINING_RECORD", "DELETE_RECORD"),
    "REDACTED": ("REPORT_EXPOSED_PII", "REDACT_RECORD"),
    "ANONYMIZED": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD"),
    "DISABLED": ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW"),
    "BLOCKED": ("REPORT_RESTORE_RISK", "BLOCK_RESTORE"),
}

# The subject's account: always verified, even when it needed no remediation.
ACCOUNT_CATEGORY: Final = "USER_PROFILE"


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
    """Evidence id for a finding or a COMPLETE verification, or "" when nothing qualifies."""
    late = set(obligation.resource.late_evidence_ids)
    mentions = [
        f
        for f in store.by_resource(obligation.resource_id)
        if f.captured_at <= audit_at and f.observed_at <= audit_at and f.evidence_id not in late
    ]
    if mentions:
        latest = max(mentions, key=lambda f: (f.captured_at, f.observed_at, f.evidence_id))
        return latest.evidence_id
    if obligation.via_coverage and obligation.discharged_by is not None:
        snap = obligation.discharged_by
        if snap.captured_at <= audit_at and snap.evidence_id not in late:
            return snap.evidence_id
    return ""


def _step(operation: str, ob: Obligation, evidence_id: str) -> Step:
    spec = OPERATIONS[operation]
    return Step(spec.phase, ob.system_id, ob.resource_id, operation, spec.state, evidence_id)


def _complete_verify_op(ob: Obligation) -> str:
    # A REDACT / ANONYMIZE record that is gone is verified as absent.
    if ob.observed_state == ABSENT and ob.required_state in ("REDACTED", "ANONYMIZED"):
        return "VERIFY_RECORD_ABSENT"
    return ob.template.verify_operation


def project(certificate: Certificate, store: FactStore, audit_at: datetime) -> list[Step]:
    if certificate.verdict is Verdict.NOT_APPLICABLE:
        return []
    steps: dict[tuple[int, str, str], Step] = {}

    def put(s: Step) -> None:
        steps.setdefault(s.sort_key(), s)

    if certificate.verdict is Verdict.INCOMPLETE:
        for ob in certificate.obligations:
            if ob.status is ObligationStatus.OPEN:
                finding, remediation = _OPEN_STEPS[ob.required_state]
                put(_step(finding, ob, cite(ob, store, audit_at)))
                put(_step(remediation, ob, ""))
                put(_step(ob.template.verify_operation, ob, ""))
            elif ob.category == ACCOUNT_CATEGORY or ob.required_state == "PRESENT":
                put(_step(ob.template.verify_operation, ob, ""))
    else:
        for ob in certificate.obligations:
            if ob.status is not ObligationStatus.DISCHARGED:
                continue
            evidence = cite(ob, store, audit_at)
            if evidence:
                put(_step(_complete_verify_op(ob), ob, evidence))
    return list(steps.values())
