"""COMPLETE is never written when a retained record is missing or an evidenced resource has no category (iteration 25).

Two reproductions from the external audit:
  1. a retained SECURITY_AUDIT_LOG record that is absent at audit_at, next to a discharged account;
  2. a resource with evidence at audit_at but no category, next to a discharged account.
Before the gate both produced COMPLETE with steps for the account only.
"""

from __future__ import annotations

import pytest

from pcda.category import CategoryResolver
from pcda.contract import OUTPUT_COLUMNS
from pcda.facts import FactKind, FactStore
from pcda.lineage import LineageGraph
from pcda.planner import project
from pcda.solver import Verdict, solve
from pcda.validator import ContractViolation, validate_output
from tests.test_failure_handling import row
from tests.test_policy_solver import AUDIT, DELETION, TEMPLATES, case, fact, res

ACCOUNT_GONE = fact("ev_a:1", "account_1", FactKind.ABSENCE, "2026-09-05T10:00:00Z", table="customer_profiles")


def run(facts: list, resources: list, block: bool = True):
    store = FactStore(facts).as_of(AUDIT)
    cert = solve(case(), DELETION, "user_1", resources, store, TEMPLATES, LineageGraph([]), full_store=store,
                 resolver=CategoryResolver(None), block_unproven_complete=block)
    return cert, store


def rejected(cert, store) -> None:
    """The row the pipeline would write for this certificate fails validation, so nothing is written."""
    steps = project(cert, store, AUDIT)
    assert steps == []
    with pytest.raises(ContractViolation, match="steps_nonempty"):
        validate_output(OUTPUT_COLUMNS, [row("case_x", cert.verdict.value, [])], expected_case_ids=["case_x"],
                        audit_at={"case_x": AUDIT}, evidence_times={})


def test_a_missing_retained_record_blocks_complete() -> None:
    gone = fact("ev_a:2", "audit_record_1", FactKind.ABSENCE, "2026-09-05T10:00:00Z", system="security_service", table="audit_records")
    resources = [res("account_1"), res("audit_record_1", "security_service")]
    cert, store = run([ACCOUNT_GONE, gone], resources)
    assert "retained_missing:audit_record_1" in cert.flags
    assert cert.verdict is Verdict.INCOMPLETE
    assert any(f.startswith("complete_unprovable:") for f in cert.flags)
    rejected(cert, store)
    # The ungated reading (what deterministic mode and the old code did) is COMPLETE.
    assert run([ACCOUNT_GONE, gone], resources, block=False)[0].verdict is Verdict.COMPLETE


def test_an_evidenced_resource_with_no_category_blocks_complete() -> None:
    mystery = fact("ev_a:3", "object_7", FactKind.PRESENCE, "2026-09-05T10:00:00Z", system="object_storage", table="screen:Unknown")
    resources = [res("account_1"), res("object_7", "object_storage")]
    cert, store = run([ACCOUNT_GONE, mystery], resources)
    assert "unresolved_evidenced:object_7" in cert.flags
    assert cert.verdict is Verdict.INCOMPLETE
    rejected(cert, store)
    assert run([ACCOUNT_GONE, mystery], resources, block=False)[0].verdict is Verdict.COMPLETE


def test_an_unknown_category_with_no_evidence_does_not_block() -> None:
    """Only evidenced resources block: a resource seen in no fact at audit_at stays a flag."""
    cert, _ = run([ACCOUNT_GONE], [res("account_1"), res("object_7", "object_storage")])
    assert "category_unknown:object_7" in cert.flags and not any(f.startswith("unresolved_evidenced") for f in cert.flags)
    assert cert.verdict is Verdict.COMPLETE


def test_citation_gate_flags_evidence_that_does_not_mention_the_resource() -> None:
    from types import SimpleNamespace

    from pcda.citations import CitationChecker

    store = FactStore([fact("ev_ok:1", "search_1", FactKind.PRESENCE, "2026-09-05T10:00:00Z", system="search_service"),
                       fact("ev_other:1", "search_2", FactKind.PRESENCE, "2026-09-05T10:00:00Z", system="search_service")])
    checker = CitationChecker(store, LineageGraph([]), {})

    def step(evidence: str, phase: str = "FINDING") -> SimpleNamespace:
        return SimpleNamespace(phase=phase, evidence_id=evidence, resource_id="search_1", system_id="search_service",
                               operation="REPORT_REMAINING_RECORD")

    assert checker.violations("case_x", Verdict.INCOMPLETE, "user_1", [step("ev_ok")]) == []
    (v,) = checker.violations("case_x", Verdict.INCOMPLETE, "user_1", [step("ev_other")])
    assert v.check == "citation_mentions" and "search_1" in v.message
    # remediation and future verification carry no citation; a COMPLETE verification does
    assert checker.violations("case_x", Verdict.INCOMPLETE, "user_1", [step("", "REMEDIATION"), step("ev_other", "VERIFICATION")]) == []
    assert len(checker.violations("case_x", Verdict.COMPLETE, "user_1", [step("ev_other", "VERIFICATION")])) == 1
