"""Custom evaluation cases built as dataset overlays.

Each case copies dataset/ into a temporary directory, applies one targeted
mutation, runs the real pipeline on one case:

    python code/main.py --mode deterministic --split sample --case <id> \
        --dataset-dir <overlay> --out <overlay>/out.csv

and compares the row with the expected verdict and steps written below.
Expectations are literal: a mismatch is a pipeline bug, not a test to relax.
The base cases are public sample cases the pipeline matches exactly, so an
"unchanged" expectation is that sample's published answer.

    python code/evaluation/custom_cases.py          # run all, print a summary
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_DIR))

from pcda import paths

Step = tuple[str, str, str, str, str, str]  # phase, system_id, resource_id, operation, state, evidence_id


@dataclass(frozen=True)
class CustomCase:
    name: str
    case_id: str
    tests: str  # what the case tests
    why: str  # why it matters for this challenge
    mutation: str  # what the overlay changes, in words
    mutate: Callable[[Path], None]
    expected_status: str
    expected_steps: tuple[Step, ...]
    must_not_contain: tuple[str, ...] = ()  # resource_ids that must never appear


@dataclass
class CustomResult:
    case: CustomCase
    status: str
    steps: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def expected_rows(self) -> list[dict]:
        keys = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")
        return [dict(zip(keys, s, strict=True), step_no=i) for i, s in enumerate(self.case.expected_steps, start=1)]

    @property
    def passed(self) -> bool:
        if self.error or self.status != self.case.expected_status:
            return False
        strip = lambda rows: [{k: r[k] for k in ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")} for r in rows]  # noqa: E731
        if strip(self.steps) != strip(self.expected_rows):
            return False
        return not any(s["resource_id"] in self.case.must_not_contain for s in self.steps)


# -- mutation helpers -------------------------------------------------------------------------------


def _rewrite_request(root: Path, case_id: str, **fields: str) -> None:
    path = root / "sample_deletion_requests.csv"
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames
        rows = list(reader)
    hit = [r for r in rows if r["case_id"] == case_id]
    assert len(hit) == 1, case_id
    hit[0].update(fields)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(rows)


def _add_evidence(root: Path, file_id: str, rel_path: str, origin: str, captured_at: str, doc: dict) -> None:
    (root / rel_path).parent.mkdir(parents=True, exist_ok=True)
    (root / rel_path).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    with (root / "evidence_files.csv").open("a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow([file_id, rel_path, origin, captured_at])


def _evidence_path(root: Path, file_id: str) -> Path:
    with (root / "evidence_files.csv").open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["file_id"] == file_id:
                return root / r["file_path"]
    raise KeyError(file_id)


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path.name, old, text.count(old))
    path.write_text(text.replace(old, new), encoding="utf-8")


def m_marketing_only(root: Path) -> None:
    _rewrite_request(root, "case_05", request_text="Please remove me from marketing lists only; keep my account.")


def m_post_audit_deletion(root: Path) -> None:
    # case_24 audit_at 2026-09-15T12:00Z. A ledger row one day later says search_5577 is gone.
    _add_evidence(root, "evidence_9001", "evidence/records/custom_post_audit_ledger.json",
                  "custom_overlay", "2026-09-16T12:00:00Z", {
                      "exported_at": "2026-09-16T12:00:00Z",
                      "origin": "custom_overlay",
                      "records": [{
                          "recorded_at": "2026-09-16T12:00:00Z",
                          "system": "search_service",
                          "resource_id": "search_5577",
                          "record_type": "SEARCH_INDEX_ENTRY",
                          "result": "NOT_FOUND",
                          "subject_reference": "keiko.nakamura@gmail.com",
                      }],
                  })


def m_replica_drift(root: Path) -> None:
    # evidence_05 holds search_1007 in both the primary and the read replica; drop the primary row.
    sql = _evidence_path(root, "evidence_05")
    lines = sql.read_text(encoding="utf-8").splitlines(keepends=True)
    primary = [i for i, line in enumerate(lines)
               if line.startswith("INSERT INTO \"customer_data_primary\" VALUES('search_1007',")]
    assert len(primary) == 1
    del lines[primary[0]]
    sql.write_text("".join(lines), encoding="utf-8")


def m_decoy_identity(root: Path) -> None:
    # case_06 subject is jonas.berg@outlook.com; a search entry carries one extra character.
    _add_evidence(root, "evidence_9002", "evidence/records/custom_decoy_ledger.json",
                  "custom_overlay", "2026-09-05T12:00:00Z", {
                      "exported_at": "2026-09-05T12:00:00Z",
                      "origin": "custom_overlay",
                      "records": [{
                          "recorded_at": "2026-09-05T12:00:00Z",
                          "system": "search_service",
                          "resource_id": "search_9999",
                          "record_type": "SEARCH_INDEX_ENTRY",
                          "result": "FOUND",
                          "subject_reference": "jonas.bergg@outlook.com",
                      }],
                  })


def m_historical_email_request(root: Path) -> None:
    # user_10's emails: nadia.ionescu@ -> nadia.petrescu@ (audit_121) -> n.petrescu@ (audit_238).
    _rewrite_request(root, "case_10", email="NADIA.PETRESCU@YAHOO.COM")


def m_historical_email_evidence(root: Path) -> None:
    # The only link from support_002 to user_10 is lookup_key=<oldest email> in support_replica.log.
    _replace_once(_evidence_path(root, "evidence_61"),
                  "lookup_key=nadia.ionescu@yahoo.com", "lookup_key=NADIA.IONESCU@YAHOO.COM")


def m_tombstone_pii(root: Path) -> None:
    bundle = _evidence_path(root, "evidence_51")
    doc = json.loads(bundle.read_text(encoding="utf-8"))
    hits = 0
    for snap in doc["snapshots"]:
        for rec in snap["records"]:
            if rec.get("resource_id") == "tombstone_440":
                rec["name"], rec["email"] = "Ada Eze", "a.eze08@gmail.com"
                hits += 1
    assert hits == 1
    bundle.write_text(json.dumps(doc, indent=1), encoding="utf-8")


def m_activity_pick_other_owner(root: Path) -> None:
    # case_10's account_010 is linked only by activity_3595 (deletion_worker, 0.6 min from reported_at).
    # A profile ledger inside the request window now records account_010 under user_02's email.
    _add_evidence(root, "evidence_9003", "evidence/records/custom_profile_ledger.json",
                  "custom_overlay", "2026-09-01T09:00:00Z", {
                      "exported_at": "2026-09-01T09:00:00Z",
                      "origin": "custom_overlay",
                      "records": [{
                          "recorded_at": "2026-09-01T09:00:00Z",
                          "system": "account_service",
                          "resource_id": "account_010",
                          "record_type": "USER_PROFILE",
                          "result": "FOUND",
                          "subject_reference": "mateo.rios@outlook.com",
                      }],
                  })


F, R, V = "FINDING", "REMEDIATION", "VERIFICATION"

CASES: tuple[CustomCase, ...] = (
    CustomCase(
        "1 non-deletion request", "case_05",
        tests="Intake recognizes a marketing opt-out that explicitly keeps the account.",
        why="A request that does not authorize deletion must be NOT_APPLICABLE with no steps, even though the "
            "case's evidence still shows a remaining account (case_05 is INCOMPLETE as a deletion).",
        mutation="request_text -> 'Please remove me from marketing lists only; keep my account.'",
        mutate=m_marketing_only, expected_status="NOT_APPLICABLE", expected_steps=(),
    ),
    CustomCase(
        "2 post-audit deletion", "case_24",
        tests="Evidence observed after audit_at cannot change the answer.",
        why="The spec fixes the answer at audit_at; a later 'deleted' observation must neither discharge the "
            "remaining search entry nor be cited.",
        mutation="new ledger evidence_9001 captured and recorded 2026-09-16 (audit_at + 1 day): search_5577 NOT_FOUND",
        mutate=m_post_audit_deletion, expected_status="INCOMPLETE",
        expected_steps=(
            (F, "account_service", "account_5334", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_92"),
            (F, "search_service", "search_5577", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_92"),
            (R, "account_service", "account_5334", "DELETE_RECORD", "ABSENT", ""),
            (R, "search_service", "search_5577", "DELETE_RECORD", "ABSENT", ""),
            (V, "account_service", "account_5334", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
            (V, "search_service", "search_5577", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
            (V, "security_service", "audit_record_7095", "VERIFY_RECORD_PRESENT", "PRESENT", ""),
        ),
    ),
    CustomCase(
        "3 replica drift", "case_16",
        tests="A row deleted from the primary but still in the read replica is a remaining record.",
        why="Deletion workflows often clean the primary only; the dataset models replica drift explicitly.",
        mutation="evidence_05: delete the customer_data_primary INSERT for search_1007, keep the replica row",
        mutate=m_replica_drift, expected_status="INCOMPLETE",
        expected_steps=(
            (F, "account_service", "account_994", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_05"),
            (F, "search_service", "search_1007", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_05"),
            (R, "account_service", "account_994", "DELETE_RECORD", "ABSENT", ""),
            (R, "search_service", "search_1007", "DELETE_RECORD", "ABSENT", ""),
            (V, "account_service", "account_994", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
            (V, "search_service", "search_1007", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
            (V, "security_service", "audit_record_9528", "VERIFY_RECORD_PRESENT", "PRESENT", ""),
        ),
    ),
    CustomCase(
        "4 decoy identity", "case_06",
        tests="A near-identical email (one extra character) on another resource is not the subject's.",
        why="Identity matching must be exact; fuzzy matching would delete someone else's data.",
        mutation="new ledger evidence_9002 (2026-09-05, inside the request window): search_9999 FOUND for jonas.bergg@outlook.com",
        mutate=m_decoy_identity, expected_status="INCOMPLETE",
        expected_steps=(
            (F, "search_service", "search_001", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_57"),
            (R, "search_service", "search_001", "DELETE_RECORD", "ABSENT", ""),
            (V, "account_service", "account_006", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
            (V, "search_service", "search_001", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
        ),
        must_not_contain=("search_9999",),
    ),
    CustomCase(
        "5a historical email in the request, uppercased", "case_10",
        tests="A request filed under an intermediate historical email, in upper case, resolves to the same subject.",
        why="Requests may come from any past address; normalization and the audit-log closure must both hold.",
        mutation="request email -> NADIA.PETRESCU@YAHOO.COM (user_10's second email, audit_121/audit_238)",
        mutate=m_historical_email_request, expected_status="INCOMPLETE",
        expected_steps=(
            (F, "support_platform", "support_002", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_61"),
            (R, "support_platform", "support_002", "DELETE_RECORD", "ABSENT", ""),
            (V, "account_service", "account_010", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
            (V, "support_platform", "support_002", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
        ),
    ),
    CustomCase(
        "5b historical email in evidence, uppercased", "case_10",
        tests="Evidence that names the subject only by an old email, in upper case, still attributes the resource.",
        why="Legacy systems keep old identifiers in arbitrary case; support_002's only link is that old email.",
        mutation="evidence_61: lookup_key=nadia.ionescu@yahoo.com -> NADIA.IONESCU@YAHOO.COM",
        mutate=m_historical_email_evidence, expected_status="INCOMPLETE",
        expected_steps=(
            (F, "support_platform", "support_002", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_61"),
            (R, "support_platform", "support_002", "DELETE_RECORD", "ABSENT", ""),
            (V, "account_service", "account_010", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
            (V, "support_platform", "support_002", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
        ),
    ),
    CustomCase(
        "6 tombstone with PII", "case_31",
        tests="A tombstone that regains direct identifiers is exposed PII to redact, not a record to delete.",
        why="ACCOUNT_TOMBSTONE is REDACT: keep the stable user reference, remove name/email/phone/hash.",
        mutation="evidence_51: tombstone_440 name -> 'Ada Eze', email -> a.eze08@gmail.com",
        mutate=m_tombstone_pii, expected_status="INCOMPLETE",
        expected_steps=(
            (F, "identity_service", "tombstone_440", "REPORT_EXPOSED_PII", "PRESENT", "evidence_51"),
            (R, "identity_service", "tombstone_440", "REDACT_RECORD", "REDACTED", ""),
            (V, "account_service", "account_4857", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
            (V, "security_service", "audit_record_6909", "VERIFY_RECORD_PRESENT", "PRESENT", ""),
            (V, "identity_service", "tombstone_440", "VERIFY_RECORD_REDACTED", "REDACTED", ""),
        ),
    ),
    CustomCase(
        "7 activity pick owned by another user", "case_10",
        tests="An activity_temporal pick is refused when a record of that resource names another user.",
        why="A deletion_worker row near reported_at is a timing claim, not ownership: taking it would report and "
            "delete someone else's account (iteration 14 refused 87 such test picks).",
        mutation="new ledger evidence_9003 (2026-09-01, inside the request window): account_010 FOUND for "
                 "mateo.rios@outlook.com (user_02)",
        # Without account_010, case_10's only feasible row is account_4990, which case_22 is also forced
        # to: the propagation refuses both, so case_10 gets no account at all rather than a guess.
        mutate=m_activity_pick_other_owner, expected_status="INCOMPLETE",
        expected_steps=(
            (F, "support_platform", "support_002", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_61"),
            (R, "support_platform", "support_002", "DELETE_RECORD", "ABSENT", ""),
            (V, "support_platform", "support_002", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
        ),
        must_not_contain=("account_010", "account_4990"),
    ),
)


def run_case(case: CustomCase, keep: Path | None = None) -> CustomResult:
    with tempfile.TemporaryDirectory(prefix="pcda_overlay_") as tmp:
        root = Path(tmp) / "dataset"
        shutil.copytree(paths.DATASET, root)
        case.mutate(root)
        out = Path(tmp) / "out.csv"
        proc = subprocess.run(
            [sys.executable, str(CODE_DIR / "main.py"), "--mode", "deterministic", "--split", "sample",
             "--case", case.case_id, "--dataset-dir", str(root), "--out", str(out)],
            capture_output=True, text=True, encoding="utf-8",
        )
        if proc.returncode != 0 or not out.exists():
            return CustomResult(case, "<error>", error=(proc.stderr or proc.stdout)[-800:])
        with out.open(encoding="utf-8", newline="") as fh:
            (row,) = list(csv.DictReader(fh))
        flags_path = out.with_suffix(".flags.json")
        flags = json.loads(flags_path.read_text(encoding="utf-8")).get(case.case_id, []) if flags_path.exists() else []
        if keep is not None:
            keep.mkdir(parents=True, exist_ok=True)
            shutil.copy(out, keep / f"{re.sub(r'[^a-z0-9]+', '_', case.name.lower())}.csv")
        return CustomResult(case, row["deletion_status"], json.loads(row["steps_json"]), flags)


def run_all() -> list[CustomResult]:
    return [run_case(c, keep=paths.RUNS_DIR / "custom") for c in CASES]


def main() -> int:
    results = run_all()
    for r in results:
        print(f"{'PASS' if r.passed else 'FAIL'}  {r.case.name:<48} {r.case.case_id} got {r.status}")
        if r.error:
            print("      error:", r.error)
    print(f"{sum(r.passed for r in results)}/{len(results)} custom cases pass")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
