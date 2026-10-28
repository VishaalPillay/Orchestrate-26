"""Lineage safety: a record change placed before the restore block that can recreate it is a violation.

The canonical order never produces that, so the check only fires on a misordered plan. Until
LineageGraph.affects existed the check would have raised AttributeError on the first such plan.
"""

from __future__ import annotations

from pcda.facts import parse_ts
from pcda.lineage import Claim, LineageGraph
from pcda.validator import _lineage_safety

AUDIT = parse_ts("2026-09-10T12:00:00Z")
RESTORE = Claim("activity_1", parse_ts("2026-09-01T00:00:00Z"), "RESTORE_BACKUP", "backup_service", "search_service",
                "backup_7", "SUCCESS", "Restore job repopulated downstream resource search_4882.")


def step(op: str, rid: str) -> dict[str, str]:
    return {"operation": op, "resource_id": rid}


def test_affects_follows_restore_rows_up_to_t() -> None:
    lineage = LineageGraph([RESTORE])
    assert lineage.affects("backup_7", "search_4882", AUDIT)
    assert not lineage.affects("backup_7", "search_4882", parse_ts("2026-08-01T00:00:00Z"))
    assert not lineage.affects("backup_7", "search_1", AUDIT)


def test_delete_before_the_block_that_could_restore_it_is_flagged() -> None:
    lineage = LineageGraph([RESTORE])
    bad = [step("DELETE_RECORD", "search_4882"), step("BLOCK_RESTORE", "backup_7")]
    good = [step("BLOCK_RESTORE", "backup_7"), step("DELETE_RECORD", "search_4882")]
    (v,) = _lineage_safety("case_x", bad, lineage, AUDIT)
    assert v.check == "lineage_safety" and "search_4882" in v.message
    assert _lineage_safety("case_x", good, lineage, AUDIT) == []
