"""Output invariants over every case. Gold is read only for the per-system share table.

    python code/tools/invariants.py [--out code/.cache/runs/invariants.md]

Runs the deterministic pipeline over all 550 requests (the rows equal
output.csv for test and sample_output.csv for samples) and checks:
  1. every FINDING and COMPLETE-verification evidence_id mentions the step's
     resource (a fact of it or naming it, an activity row on it, an audit-log
     row of the subject) or is a non-vacuous covering snapshot of its system;
  2. a resource in two cases' steps with different subjects is held by an
     identifier tier in both;
  3. every INCOMPLETE case verifies an account;
  4. every SECURITY_AUDIT_LOG obligation gets VERIFY_RECORD_PRESENT;
  5. per system, the share of cases with findings, test vs samples.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from pcda import paths
from pcda.cases import load_requests
from pcda.citations import CitationChecker
from pcda.identity import LinkTier, normalize_email
from pcda.llm.client import RunMode
from pcda.pipeline import build_context, run_case
from pcda.solver import Verdict

ACCOUNT = "account_service"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=paths.RUNS_DIR / "invariants.md")
    args = p.parse_args()
    load_dotenv(paths.ENV_FILE)  # the perception cache is keyed on PERCEPTION_MODEL
    ctx = build_context(RunMode.DETERMINISTIC)

    checker = CitationChecker.build(ctx.store, ctx.lineage)
    mentions = checker.mentions

    splits = {"test": load_requests(paths.DELETION_REQUESTS), "sample": load_requests(paths.SAMPLE_REQUESTS)}
    v1: list[str] = []
    v3: list[str] = []
    v4: list[str] = []
    holders: dict[str, dict[str, list[tuple[str, LinkTier]]]] = defaultdict(lambda: defaultdict(list))
    findings_by_system: dict[str, Counter[str]] = {s: Counter() for s in splits}
    deletion_cases: Counter[str] = Counter()
    for split, cases in splits.items():
        for case in cases:
            r = run_case(case, ctx)
            subject = ctx.closure.subject_of(normalize_email(case.email))
            verdict = r.certificate.verdict
            if verdict is Verdict.NOT_APPLICABLE:
                continue
            deletion_cases[split] += 1
            tier = {a.resource_id: a.tier for a in r.attributions}
            for s in r.steps:
                cited = s.phase == "FINDING" or (verdict is Verdict.COMPLETE and s.phase == "VERIFICATION")
                if cited and s.evidence_id and not mentions(s.evidence_id, s.resource_id, s.system_id, subject):
                    v1.append(f"{split} {case.case_id} {s.operation} {s.resource_id} cites {s.evidence_id}")
                if s.resource_id in tier and subject is not None:
                    holders[s.resource_id][subject].append((case.case_id, tier[s.resource_id]))
            for sys_id in sorted({s.system_id for s in r.steps if s.phase == "FINDING"}):
                findings_by_system[split][sys_id] += 1
            if verdict is Verdict.INCOMPLETE and not any(
                s.system_id == ACCOUNT and s.phase == "VERIFICATION" for s in r.steps
            ):
                why = "no account attributed" if not any(a.system_id == ACCOUNT for a in r.attributions) else "account attributed, not verified"
                v3.append(f"{split} {case.case_id}: {why}")
            verified = {s.resource_id for s in r.steps if s.operation == "VERIFY_RECORD_PRESENT"}
            for o in r.certificate.obligations:
                if o.category == "SECURITY_AUDIT_LOG" and o.resource_id not in verified:
                    v4.append(f"{split} {case.case_id} {verdict.value} {o.resource_id} status={o.status.value}")

    identifier = {LinkTier.DB_ROW, LinkTier.LEDGER, LinkTier.CATALOG_LINK, LinkTier.LOG_TRACE}
    v2 = []
    for rid, by_subject in sorted(holders.items()):
        if len(by_subject) < 2:
            continue
        held = [(subj, cid, t) for subj, xs in by_subject.items() for cid, t in xs]
        if any(t not in identifier for _, _, t in held):
            v2.append(f"{rid}: " + ", ".join(f"{cid}/{subj}/{t.name}" for subj, cid, t in sorted(held, key=lambda h: h[1])))

    with paths.SAMPLE_OUTPUT.open(encoding="utf-8", newline="") as fh:
        gold_rows = list(csv.DictReader(fh))
    gold = Counter()
    for row in gold_rows:
        for sys_id in {s["system_id"] for s in json.loads(row["steps_json"]) if s["phase"] == "FINDING"}:
            gold[sys_id] += 1
    n_gold = sum(1 for row in gold_rows if row["deletion_status"] != "NOT_APPLICABLE")

    lines = ["# Output invariants (deterministic)", ""]
    for title, items in (
        ("1. Cited evidence mentions the resource", v1),
        ("2. Resource in two subjects' steps without identifier links in both", v2),
        ("3. INCOMPLETE case without an account verification", v3),
        ("4. SECURITY_AUDIT_LOG obligation without VERIFY_RECORD_PRESENT", v4),
    ):
        lines += [f"## {title}: {len(items)} violation(s)", ""] + [f"- {x}" for x in items] + [""]
    lines += ["## 5. Share of deletion cases with a finding on each system", "",
              f"Test n={deletion_cases['test']}, sample n={deletion_cases['sample']}, gold n={n_gold}. "
              "Flagged when the test share is outside 0.5x-2x of the sample share.", "",
              "| system | test | sample (pred) | sample (gold) | flag |", "|---|---|---|---|---|"]
    for sys_id in sorted(set(findings_by_system["test"]) | set(findings_by_system["sample"]) | set(gold)):
        t = findings_by_system["test"][sys_id] / deletion_cases["test"]
        s = findings_by_system["sample"][sys_id] / deletion_cases["sample"]
        g = gold[sys_id] / n_gold
        flag = "" if s and 0.5 <= t / s <= 2 else ("test only" if not s else ("over" if t > 2 * s else "under"))
        lines.append(f"| {sys_id} | {t:.2f} | {s:.2f} | {g:.2f} | {flag} |")
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(line for line in lines if not line.startswith("- ")))
    for title, items in (("v1", v1), ("v2", v2), ("v3", v3), ("v4", v4)):
        for x in items[:6]:
            print(f"  {title}: {x}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
