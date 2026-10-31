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


def test_incomplete_with_no_steps_is_rejected_unconditionally() -> None:
    """The solver's fallback_no_provable_obligation (INCOMPLETE, no steps) must fail loudly, never be written."""
    for status in ("INCOMPLETE", "COMPLETE"):
        with pytest.raises(ContractViolation, match="steps_nonempty"):
            check([row("case_1", status, [])], ["case_1"])
    check([row("case_1", "NOT_APPLICABLE", [])], ["case_1"])  # the only status that may be empty


# --- the INCOMPLETE pairing rules and step field types (iteration 24) -----------------------------------------

from pcda.contract import OPERATIONS, canonical_sort_key  # noqa: E402

PAIRINGS = {
    "REPORT_REMAINING_RECORD": ("DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "REPORT_EXPOSED_PII": ("REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "REPORT_EXPOSED_PII+anonymize": ("ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    "REPORT_ACTIVE_DATA_FLOW": ("DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    "REPORT_RESTORE_RISK": ("BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
}


def step(op: str, resource: str = "r_1", system: str = "s_service", evidence: str | None = None) -> dict[str, Any]:
    spec = OPERATIONS[op]
    if evidence is None:
        evidence = "evidence_1" if spec.phase == "FINDING" else ""
    return {"phase": spec.phase, "system_id": system, "resource_id": resource, "operation": op, "state": spec.state,
            "evidence_id": evidence}


def numbered(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(steps, key=lambda s: canonical_sort_key(s["operation"], s["system_id"], s["resource_id"]))
    return [{**s, "step_no": i} for i, s in enumerate(ordered, start=1)]


@pytest.mark.parametrize("kind", sorted(PAIRINGS))
def test_each_finding_remediation_verification_chain_is_accepted(kind: str) -> None:
    remediation, verification = PAIRINGS[kind]
    finding = kind.split("+")[0]
    check([row("case_1", "INCOMPLETE", numbered([step(finding), step(remediation), step(verification)]))], ["case_1"])


def test_a_finding_without_its_remediation_is_rejected() -> None:
    with pytest.raises(ContractViolation, match="finding_has_remediation"):
        check([row("case_1", "INCOMPLETE", numbered([step("REPORT_REMAINING_RECORD")]))], ["case_1"])
    # the remediation exists, but on another resource
    steps = numbered([step("REPORT_REMAINING_RECORD"), step("DELETE_RECORD", "r_2"), step("VERIFY_RECORD_ABSENT", "r_2")])
    with pytest.raises(ContractViolation, match="finding_has_remediation"):
        check([row("case_1", "INCOMPLETE", steps)], ["case_1"])
    # an exposed-PII finding is not closed by a delete
    steps = numbered([step("REPORT_EXPOSED_PII"), step("DELETE_RECORD"), step("VERIFY_RECORD_ABSENT")])
    with pytest.raises(ContractViolation, match="finding_has_remediation"):
        check([row("case_1", "INCOMPLETE", steps)], ["case_1"])


def test_a_remediation_without_its_verification_is_rejected() -> None:
    steps = numbered([step("REPORT_REMAINING_RECORD"), step("DELETE_RECORD")])
    with pytest.raises(ContractViolation, match="remediation_has_verification"):
        check([row("case_1", "INCOMPLETE", steps)], ["case_1"])


def test_step_no_must_be_an_int_not_a_float_or_bool() -> None:
    good = numbered([step("REPORT_REMAINING_RECORD"), step("DELETE_RECORD"), step("VERIFY_RECORD_ABSENT")])
    for bad_no in (1.0, True, "1", None):
        steps = [{**good[0], "step_no": bad_no}, *good[1:]]
        with pytest.raises(ContractViolation, match="step_no"):
            check([row("case_1", "INCOMPLETE", steps)], ["case_1"])


@pytest.mark.parametrize("bad_value", [[], {}, None, 7, ["DELETE_RECORD"]])
@pytest.mark.parametrize("field", ["operation", "phase", "system_id", "resource_id", "state", "evidence_id"])
def test_a_mistyped_field_is_a_violation_never_a_type_error(field: str, bad_value: Any) -> None:
    good = numbered([step("REPORT_REMAINING_RECORD"), step("DELETE_RECORD"), step("VERIFY_RECORD_ABSENT")])
    steps = [{**good[0], field: bad_value}, *good[1:]]
    with pytest.raises(ContractViolation, match="field_type"):
        check([row("case_1", "INCOMPLETE", steps)], ["case_1"])


# --- reverse pairing, row field types, atomic output, zip hygiene (iteration 25) -----------------------------


def test_a_remediation_without_its_finding_is_rejected() -> None:
    orphan = numbered([step("REPORT_REMAINING_RECORD"), step("DELETE_RECORD"), step("VERIFY_RECORD_ABSENT"),
                       step("DELETE_RECORD", "r_2"), step("VERIFY_RECORD_ABSENT", "r_2")])
    with pytest.raises(ContractViolation, match="remediation_has_finding"):
        check([row("case_1", "INCOMPLETE", orphan)], ["case_1"])
    # a retained or account verification needs no finding
    ok = numbered([step("REPORT_REMAINING_RECORD"), step("DELETE_RECORD"), step("VERIFY_RECORD_ABSENT"),
                   step("VERIFY_RECORD_PRESENT", "audit_1", "security_service"), step("VERIFY_RECORD_ABSENT", "account_1", "account_service")])
    check([row("case_1", "INCOMPLETE", ok)], ["case_1"])


@pytest.mark.parametrize("bad_row", [
    {"case_id": "case_1", "deletion_status": "INCOMPLETE", "steps_json": None},
    {"case_id": "case_1", "deletion_status": None, "steps_json": "[]"},
    {"case_id": "case_1", "deletion_status": "COMPLETE", "steps_json": 5},
    {"case_id": [], "deletion_status": "NOT_APPLICABLE", "steps_json": "[]"},
    {"case_id": {}, "deletion_status": "NOT_APPLICABLE", "steps_json": "[]"},
    {"deletion_status": "NOT_APPLICABLE", "steps_json": "[]"},
])
def test_a_mistyped_row_is_a_violation_never_a_type_error(bad_row: dict[str, Any]) -> None:
    with pytest.raises(ContractViolation):
        check([bad_row, row("case_2", "NOT_APPLICABLE", [])], ["case_1", "case_2"])


def test_outputs_are_replaced_only_after_every_one_is_written(tmp_path) -> None:
    import main as cli

    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("old a", encoding="utf-8")
    b.write_text("old b", encoding="utf-8")
    good = [{"case_id": "case_1", "deletion_status": "NOT_APPLICABLE", "steps_json": "[]"}]
    cli.commit_outputs([(a, good), (b, good)])
    assert "case_1" in a.read_text(encoding="utf-8") and "case_1" in b.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))
    # a failure while writing the second file leaves both targets as they were and no temp file behind
    a.write_text("keep a", encoding="utf-8")
    b.write_text("keep b", encoding="utf-8")
    broken = [None]  # type: ignore[list-item]
    with pytest.raises(Exception):  # noqa: B017
        cli.commit_outputs([(a, good), (b, broken)])
    assert a.read_text(encoding="utf-8") == "keep a" and b.read_text(encoding="utf-8") == "keep b"
    assert not list(tmp_path.glob("*.tmp"))


def test_package_zip_excludes_env_files_and_variants() -> None:
    from pathlib import Path

    from tools.package_zip import wanted

    for name in (".env", ".env.local", ".env.production", ".env.example.bak"):
        assert not wanted(Path("code") / name)
    assert wanted(Path("code/pcda/config.py"))
    assert not wanted(Path("code/.cache/runs/run_log.jsonl"))
