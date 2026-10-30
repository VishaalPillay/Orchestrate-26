"""Failure points without a dedicated test elsewhere: the validator, unparseable evidence, provider outage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pcda.agents.skeptic import Budget, LLMSkepticAgent, Target, run_queue
from pcda.contract import OUTPUT_COLUMNS
from pcda.facts import parse_ts
from pcda.ingest.base import IngestReport, ManifestRow
from pcda.ingest.json_adapter import JsonAdapter
from pcda.llm.client import RunMode
from pcda.validator import ContractViolation, validate_output
from tests.test_skeptic import base_world, client

AUDIT = parse_ts("2026-09-10T12:00:00Z")


def row(case_id: str, status: str, steps: list[dict[str, Any]]) -> dict[str, str]:
    return {"case_id": case_id, "deletion_status": status, "steps_json": json.dumps(steps)}


def check(rows: list[dict[str, str]], expected: list[str]) -> None:
    validate_output(
        OUTPUT_COLUMNS, rows, expected_case_ids=expected,
        audit_at={c: AUDIT for c in expected}, evidence_times={"evidence_1": parse_ts("2026-09-01T00:00:00Z")},
    )


def test_validator_accepts_a_valid_output_and_lists_every_violation_otherwise() -> None:
    ok = row("case_1", "INCOMPLETE", [
        {"step_no": 1, "phase": "FINDING", "system_id": "search_service", "resource_id": "search_1",
         "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "evidence_1"},
        {"step_no": 2, "phase": "REMEDIATION", "system_id": "search_service", "resource_id": "search_1",
         "operation": "DELETE_RECORD", "state": "ABSENT", "evidence_id": ""},
        {"step_no": 3, "phase": "VERIFICATION", "system_id": "search_service", "resource_id": "search_1",
         "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": ""},
    ])
    check([ok, row("case_2", "NOT_APPLICABLE", [])], ["case_1", "case_2"])
    bad = row("case_1", "INCOMPLETE", [
        {"step_no": 1, "phase": "FINDING", "system_id": "search_service", "resource_id": "search_1",
         "operation": "DELETE_EVERYTHING", "state": "PRESENT", "evidence_id": "evidence_99"},
    ])
    with pytest.raises(ContractViolation) as err:
        check([bad], ["case_1", "case_2"])
    text = str(err.value)
    assert "case_2: [row_present]" in text and "[row_count]" in text and "[operation_enum]" in text
    unknown_evidence = row("case_1", "INCOMPLETE", [
        {"step_no": 1, "phase": "FINDING", "system_id": "search_service", "resource_id": "search_1",
         "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "evidence_99"},
    ])
    with pytest.raises(ContractViolation, match=r"\[evidence_known\].*evidence_99"):
        check([unknown_evidence, row("case_2", "NOT_APPLICABLE", [])], ["case_1", "case_2"])


def test_unparseable_evidence_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "weird.json"
    path.write_text(json.dumps({"unexpected": True}), encoding="utf-8")
    manifest_row = ManifestRow("evidence_x", "weird.json", "test", parse_ts("2026-09-01T00:00:00Z"))
    with pytest.raises(ValueError, match="unrecognised JSON shape"):
        JsonAdapter(IngestReport()).parse(path, manifest_row)


def test_three_consecutive_provider_failures_stop_the_queue(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("SKEPTIC_MODEL", "scripted-model")
    replay, _ = client(tmp_path, [], RunMode.REPLAY)
    queue = [Target("case_1", "PICK", "account_900", i, 1.0, "t") for i in range(5)]
    adjustments, done, unreached = run_queue(LLMSkepticAgent(replay, base_world()), queue, Budget(50))
    assert [d.outcome for d in done] == ["ERROR"] * 3 and unreached == 2 and adjustments == {}


def test_incomplete_with_no_steps_is_valid_only_on_the_flagged_fallback_path() -> None:
    empty = row("case_1", "INCOMPLETE", [])
    validate_output(
        OUTPUT_COLUMNS, [empty], expected_case_ids=["case_1"], audit_at={"case_1": AUDIT},
        evidence_times={}, fallback_cases={"case_1"},
    )
    with pytest.raises(ContractViolation, match="steps_nonempty"):
        check([empty], ["case_1"])
    with pytest.raises(ContractViolation, match="steps_nonempty"):
        validate_output(
            OUTPUT_COLUMNS, [row("case_1", "COMPLETE", [])], expected_case_ids=["case_1"],
            audit_at={"case_1": AUDIT}, evidence_times={}, fallback_cases={"case_1"},
        )
