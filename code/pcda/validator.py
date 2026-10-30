"""The output contract as executable checks.

validate_output collects every violation, then raises once. Callers never
write output.csv when it raises. When a LineageGraph is supplied, it also
proves the canonical order is dependency-safe: every DISABLE_DATA_FLOW /
BLOCK_RESTORE that affects a resource precedes that resource's DELETE,
REDACT or ANONYMIZE.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pcda.contract import (
    FINDING_REMEDIATIONS,
    GUARD_OPERATIONS,
    OPERATIONS,
    OUTPUT_COLUMNS,
    PHASE_FINDING,
    PHASE_VERIFICATION,
    PHASES,
    RECORD_CHANGE_OPERATIONS,
    REMEDIATION_VERIFICATION,
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    STATUS_NOT_APPLICABLE,
    STATUSES,
    STEP_FIELDS,
    canonical_sort_key,
)
from pcda.lineage import LineageGraph

_STRING_FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")


@dataclass(frozen=True)
class Violation:
    case_id: str
    check: str
    message: str

    def __str__(self) -> str:
        return f"{self.case_id}: [{self.check}] {self.message}"


class ContractViolation(Exception):
    def __init__(self, violations: Sequence[Violation]) -> None:
        self.violations = list(violations)
        shown = "\n".join(f"  {v}" for v in self.violations[:50])
        more = len(self.violations) - 50
        tail = f"\n  ... {more} more" if more > 0 else ""
        super().__init__(f"{len(self.violations)} contract violation(s):\n{shown}{tail}")

    @property
    def flagged_cases(self) -> list[str]:
        return sorted({v.case_id for v in self.violations})


def validate_row(
    row: Mapping[str, str],
    *,
    audit_at: datetime | None,
    evidence_times: Mapping[str, datetime],
    lineage: LineageGraph | None = None,
) -> list[Violation]:
    case_id = row.get("case_id", "?")
    out: list[Violation] = []

    def bad(check: str, message: str) -> None:
        out.append(Violation(case_id, check, message))

    status = row.get("deletion_status", "")
    if status not in STATUSES:
        bad("status_enum", f"deletion_status {status!r}")
        return out

    try:
        steps: Any = json.loads(row.get("steps_json", ""))
    except json.JSONDecodeError as exc:
        bad("json_valid", str(exc))
        return out
    if not isinstance(steps, list):
        bad("json_array", "steps_json is not a JSON array")
        return out

    if status == STATUS_NOT_APPLICABLE:
        if steps:
            bad("not_applicable_empty", "NOT_APPLICABLE must have steps_json=[]")
        return out
    if not steps:
        # Unconditional: a case that reaches fallback_no_provable_obligation (INCOMPLETE, no steps) fails
        # here loudly instead of being written.
        bad("steps_nonempty", f"{status} with no steps")
        return out

    for i, s in enumerate(steps, start=1):
        if not isinstance(s, dict) or set(s) != set(STEP_FIELDS):
            got = sorted(s) if isinstance(s, dict) else type(s).__name__
            bad("step_fields", f"step {i} fields {got}")
            return out
        # Types first, so no lookup below can raise on malformed input (an unhashable operation, say).
        if type(s["step_no"]) is not int:  # bool and float are not step numbers
            bad("step_no", f"step {i} step_no {s['step_no']!r} is not an integer")
        elif s["step_no"] != i:
            bad("step_no", f"step {i} has step_no {s['step_no']!r}")
        mistyped = [k for k in _STRING_FIELDS if not isinstance(s[k], str)]
        for k in mistyped:
            bad("field_type", f"step {i} {k} is not a string")
        if mistyped:
            continue
        spec = OPERATIONS.get(s["operation"])
        if spec is None:
            bad("operation_enum", f"step {i} operation {s['operation']!r}")
            continue
        if s["phase"] != spec.phase:
            bad("operation_phase", f"step {i} {s['operation']} in phase {s['phase']}")
        if s["state"] != spec.state:
            bad("operation_state", f"step {i} {s['operation']} with state {s['state']}")
        if not s["system_id"] or not s["resource_id"]:
            bad("ids_nonempty", f"step {i} missing system_id or resource_id")
    if out:
        return out

    phase_idx = [PHASES.index(s["phase"]) for s in steps]
    if phase_idx != sorted(phase_idx):
        bad("phase_order", "phases not FINDING -> REMEDIATION -> VERIFICATION")
    keys = [canonical_sort_key(s["operation"], s["system_id"], s["resource_id"]) for s in steps]
    if keys != sorted(keys):
        bad("canonical_order", "steps not in (operation, system_id, resource_id) order")
    if len(set(keys)) != len(keys):
        bad("duplicate_step", "same operation on the same resource twice")

    if status == STATUS_COMPLETE:
        for i, s in enumerate(steps, start=1):
            if s["phase"] != PHASE_VERIFICATION:
                bad("complete_verification_only", f"step {i} is {s['phase']}")
            if not s["evidence_id"]:
                bad("complete_cites_evidence", f"step {i} has no evidence_id")
    elif status == STATUS_INCOMPLETE:
        if not any(s["phase"] == PHASE_FINDING for s in steps):
            bad("incomplete_has_finding", "INCOMPLETE with no FINDING step")
        planned = {(s["operation"], s["system_id"], s["resource_id"]) for s in steps}
        for i, s in enumerate(steps, start=1):
            on = (s["system_id"], s["resource_id"])
            if s["operation"] in FINDING_REMEDIATIONS and not any((r, *on) in planned for r in FINDING_REMEDIATIONS[s["operation"]]):
                bad("finding_has_remediation", f"step {i} {s['operation']} on {on[1]} has no {'/'.join(sorted(FINDING_REMEDIATIONS[s['operation']]))}")
            if s["operation"] in REMEDIATION_VERIFICATION and (REMEDIATION_VERIFICATION[s["operation"]], *on) not in planned:
                bad("remediation_has_verification", f"step {i} {s['operation']} on {on[1]} has no {REMEDIATION_VERIFICATION[s['operation']]}")
        for i, s in enumerate(steps, start=1):
            if s["phase"] == PHASE_FINDING and not s["evidence_id"]:
                bad("finding_cites_evidence", f"step {i} has no evidence_id")
            if s["phase"] != PHASE_FINDING and s["evidence_id"]:
                bad("future_step_no_evidence", f"step {i} {s['phase']} cites {s['evidence_id']}")

    for i, s in enumerate(steps, start=1):
        eid = s["evidence_id"]
        if not eid:
            continue
        seen_at = evidence_times.get(eid)
        if seen_at is None:
            bad("evidence_known", f"step {i} cites unknown id {eid}")
        elif audit_at is not None and seen_at > audit_at:
            bad("evidence_before_audit", f"step {i} cites {eid} captured after audit_at")

    if lineage is not None and audit_at is not None:
        out.extend(_lineage_safety(case_id, steps, lineage, audit_at))
    return out


def _lineage_safety(
    case_id: str, steps: list[dict[str, Any]], lineage: LineageGraph, audit_at: datetime
) -> list[Violation]:
    out: list[Violation] = []
    guards = [(i, s) for i, s in enumerate(steps) if s["operation"] in GUARD_OPERATIONS]
    changes = [(i, s) for i, s in enumerate(steps) if s["operation"] in RECORD_CHANGE_OPERATIONS]
    for gi, g in guards:
        for ci, c in changes:
            if ci < gi and lineage.affects(g["resource_id"], c["resource_id"], audit_at):
                out.append(
                    Violation(
                        case_id,
                        "lineage_safety",
                        f"{c['operation']} {c['resource_id']} (step {ci + 1}) precedes "
                        f"{g['operation']} {g['resource_id']} (step {gi + 1}) that can recreate it",
                    )
                )
    return out


def validate_output(
    header: Sequence[str],
    rows: Sequence[Mapping[str, str]],
    *,
    expected_case_ids: Sequence[str],
    audit_at: Mapping[str, datetime],
    evidence_times: Mapping[str, datetime],
    lineage: LineageGraph | None = None,
) -> None:
    """Raise ContractViolation listing every problem; return None when the output is valid."""
    violations: list[Violation] = []
    if tuple(header) != OUTPUT_COLUMNS:
        violations.append(Violation("*", "columns", f"header {list(header)}"))
    ids = [r.get("case_id", "") for r in rows]
    if ids != sorted(ids):
        violations.append(Violation("*", "sorted", "rows not sorted by case_id"))
    if len(ids) != len(set(ids)):
        violations.append(Violation("*", "unique", "duplicate case_id rows"))
    expected = set(expected_case_ids)
    for missing in sorted(expected - set(ids)):
        violations.append(Violation(missing, "row_present", "no output row"))
    for extra in sorted(set(ids) - expected):
        violations.append(Violation(extra, "row_expected", "row for unknown case_id"))
    if len(rows) != len(expected_case_ids):
        violations.append(
            Violation("*", "row_count", f"{len(rows)} rows, expected {len(expected_case_ids)}")
        )
    for r in rows:
        cid = r.get("case_id", "")
        violations.extend(
            validate_row(r, audit_at=audit_at.get(cid), evidence_times=evidence_times, lineage=lineage)
        )
    if violations:
        raise ContractViolation(violations)
