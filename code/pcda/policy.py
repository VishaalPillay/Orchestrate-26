"""Compile deletion_rules.csv into obligation templates.

Required final state per action:
  DELETE -> ABSENT, REDACT -> REDACTED, ANONYMIZE -> ANONYMIZED, RETAIN -> PRESENT,
except BACKUP_ARCHIVE: RETAIN the archive, and the obligation checked is
restore BLOCKED (gold verifies backups with VERIFY_RESTORE_BLOCKED, never
VERIFY_RECORD_PRESENT). DATA_FLOW has no rule row; it gets one implicit
template whose required state is DISABLED.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pcda.category import DATA_FLOW


@dataclass(frozen=True)
class ObligationTemplate:
    rule_id: str
    category: str
    condition: str
    action: str  # DELETE | REDACT | ANONYMIZE | RETAIN | DISABLE (implicit)
    required_state: str  # contract state the resource must reach
    verify_operation: str  # contract VERIFY_* operation that proves it


ACTION_TO_STATE: Final[dict[str, tuple[str, str]]] = {
    "DELETE": ("ABSENT", "VERIFY_RECORD_ABSENT"),
    "REDACT": ("REDACTED", "VERIFY_RECORD_REDACTED"),
    "ANONYMIZE": ("ANONYMIZED", "VERIFY_RECORD_ANONYMIZED"),
    "RETAIN": ("PRESENT", "VERIFY_RECORD_PRESENT"),
}

CATEGORY_STATE_OVERRIDES: Final[dict[str, tuple[str, str]]] = {
    "BACKUP_ARCHIVE": ("BLOCKED", "VERIFY_RESTORE_BLOCKED"),
}

IMPLICIT_TEMPLATES: Final[tuple[ObligationTemplate, ...]] = (
    ObligationTemplate(
        rule_id="implicit_data_flow",
        category=DATA_FLOW,
        condition="An active process can copy or recreate the subject's data",
        action="DISABLE",
        required_state="DISABLED",
        verify_operation="VERIFY_DATA_FLOW_DISABLED",
    ),
)


def compile_rules(path: Path) -> dict[str, ObligationTemplate]:
    """category -> template, including IMPLICIT_TEMPLATES."""
    raise NotImplementedError("policy.compile_rules")
