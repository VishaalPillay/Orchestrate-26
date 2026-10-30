"""The output contract as data. A leaf module: it imports nothing from pcda.

Operation order is the order of the spec tables (AGENTS.md §6.4). Recon showed
every sample case fits the canonical key (operation index, system_id,
resource_id) compared as strings, so that key is the only ordering rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

OUTPUT_COLUMNS: Final[tuple[str, ...]] = ("case_id", "deletion_status", "steps_json")
STEP_FIELDS: Final[tuple[str, ...]] = (
    "step_no",
    "phase",
    "system_id",
    "resource_id",
    "operation",
    "state",
    "evidence_id",
)

STATUS_COMPLETE: Final = "COMPLETE"
STATUS_INCOMPLETE: Final = "INCOMPLETE"
STATUS_NOT_APPLICABLE: Final = "NOT_APPLICABLE"
STATUSES: Final[frozenset[str]] = frozenset(
    {STATUS_COMPLETE, STATUS_INCOMPLETE, STATUS_NOT_APPLICABLE}
)

PHASE_FINDING: Final = "FINDING"
PHASE_REMEDIATION: Final = "REMEDIATION"
PHASE_VERIFICATION: Final = "VERIFICATION"
PHASES: Final[tuple[str, ...]] = (PHASE_FINDING, PHASE_REMEDIATION, PHASE_VERIFICATION)


@dataclass(frozen=True)
class OperationSpec:
    name: str
    phase: str
    state: str
    index: int


_OPERATION_TABLE: Final[tuple[tuple[str, str, str], ...]] = (
    ("REPORT_REMAINING_RECORD", PHASE_FINDING, "PRESENT"),
    ("REPORT_EXPOSED_PII", PHASE_FINDING, "PRESENT"),
    ("REPORT_ACTIVE_DATA_FLOW", PHASE_FINDING, "ACTIVE"),
    ("REPORT_RESTORE_RISK", PHASE_FINDING, "ACTIVE"),
    ("DISABLE_DATA_FLOW", PHASE_REMEDIATION, "DISABLED"),
    ("BLOCK_RESTORE", PHASE_REMEDIATION, "BLOCKED"),
    ("DELETE_RECORD", PHASE_REMEDIATION, "ABSENT"),
    ("REDACT_RECORD", PHASE_REMEDIATION, "REDACTED"),
    ("ANONYMIZE_RECORD", PHASE_REMEDIATION, "ANONYMIZED"),
    ("VERIFY_RECORD_ABSENT", PHASE_VERIFICATION, "ABSENT"),
    ("VERIFY_RECORD_PRESENT", PHASE_VERIFICATION, "PRESENT"),
    ("VERIFY_RECORD_REDACTED", PHASE_VERIFICATION, "REDACTED"),
    ("VERIFY_RECORD_ANONYMIZED", PHASE_VERIFICATION, "ANONYMIZED"),
    ("VERIFY_DATA_FLOW_DISABLED", PHASE_VERIFICATION, "DISABLED"),
    ("VERIFY_RESTORE_BLOCKED", PHASE_VERIFICATION, "BLOCKED"),
)

OPERATIONS: Final[dict[str, OperationSpec]] = {
    name: OperationSpec(name, phase, state, i)
    for i, (name, phase, state) in enumerate(_OPERATION_TABLE)
}

# Remediation operations that stop recreation, and those that change records.
# The validator's lineage check asserts the first group precedes the second for
# every resource the flow or backup can reach.
GUARD_OPERATIONS: Final[frozenset[str]] = frozenset({"DISABLE_DATA_FLOW", "BLOCK_RESTORE"})
RECORD_CHANGE_OPERATIONS: Final[frozenset[str]] = frozenset(
    {"DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"}
)


# A finding is closed by one of these remediations on the same (system_id, resource_id), and a remediation
# is checked by its verification on the same resource (AGENTS.md section 6.3: an INCOMPLETE case carries the
# findings, the complete ordered remediation plan, and the checks that must pass after remediation).
FINDING_REMEDIATIONS: Final[dict[str, frozenset[str]]] = {
    "REPORT_REMAINING_RECORD": frozenset({"DELETE_RECORD"}),
    "REPORT_EXPOSED_PII": frozenset({"REDACT_RECORD", "ANONYMIZE_RECORD"}),
    "REPORT_ACTIVE_DATA_FLOW": frozenset({"DISABLE_DATA_FLOW"}),
    "REPORT_RESTORE_RISK": frozenset({"BLOCK_RESTORE"}),
}
REMEDIATION_VERIFICATION: Final[dict[str, str]] = {
    "DELETE_RECORD": "VERIFY_RECORD_ABSENT",
    "REDACT_RECORD": "VERIFY_RECORD_REDACTED",
    "ANONYMIZE_RECORD": "VERIFY_RECORD_ANONYMIZED",
    "DISABLE_DATA_FLOW": "VERIFY_DATA_FLOW_DISABLED",
    "BLOCK_RESTORE": "VERIFY_RESTORE_BLOCKED",
}


def canonical_sort_key(operation: str, system_id: str, resource_id: str) -> tuple[int, str, str]:
    """The single ordering rule for steps_json. Phases follow from operation index."""
    return (OPERATIONS[operation].index, system_id, resource_id)
