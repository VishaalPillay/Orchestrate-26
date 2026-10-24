"""PCDA command line.

    python code/main.py --mode deterministic --split sample
    python code/main.py --mode live --split test --workers 4
    python code/main.py --mode replay --split all

split test/all writes output.csv at the repo root; split sample writes
code/.cache/runs/sample_output.csv. Output is validated before it is written
and nothing is written when validation fails.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from pcda import paths
from pcda.cases import SPLITS, CaseRequest, load_evidence_times, load_requests
from pcda.contract import OUTPUT_COLUMNS
from pcda.llm.client import RunMode
from pcda.pipeline import CaseResult, Context, build_context, run_case
from pcda.planner import serialize_steps
from pcda.validator import ContractViolation, validate_output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="pcda", description="Ghost Data Still Exists deletion audit")
    p.add_argument("--mode", choices=[m.value for m in RunMode], default=RunMode.DETERMINISTIC.value)
    p.add_argument("--split", choices=sorted(SPLITS), default="sample")
    p.add_argument("--workers", type=int, default=1)
    return p.parse_args(argv)


def run_split(cases: list[CaseRequest], ctx: Context, workers: int) -> list[CaseResult]:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda c: run_case(c, ctx), cases))


def to_rows(results: list[CaseResult]) -> list[dict[str, str]]:
    rows = [
        {
            "case_id": r.certificate.case_id,
            "deletion_status": r.certificate.verdict.value,
            "steps_json": serialize_steps(r.steps),
        }
        for r in results
    ]
    rows.sort(key=lambda row: row["case_id"])
    return rows


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(OUTPUT_COLUMNS))
        w.writeheader()
        w.writerows(rows)


def print_canonical_systems(ctx: Context) -> None:
    print("canonical system_id by resource prefix (learned from system_activity.csv):")
    for prefix, vote in ctx.canonical.items():
        print(f"  {prefix:<16} -> {vote.system_id:<22} tier={vote.tier:<13} votes={vote.tier_votes}")
    multi = {p: v for p, v in ctx.canonical.items() if len(v.all_targets) > 1}
    print(f"prefixes with more than one target system ({len(multi)}):")
    for prefix, vote in multi.items():
        print(f"  {prefix:<16} all targets={vote.all_targets} -> {vote.system_id}")


def print_split_summary(name: str, cases: list[CaseRequest], results: list[CaseResult]) -> None:
    by_id = {c.case_id: c for c in cases}
    verdicts = Counter(r.certificate.verdict.value for r in results)
    print(f"[{name}] verdicts: {dict(sorted(verdicts.items()))}")
    not_applicable = [r for r in results if r.intake.authorizes_deletion is False]
    ambiguous = [r for r in results if r.intake.is_ambiguous]
    print(f"[{name}] intake NOT_APPLICABLE: {len(not_applicable)}")
    for r in not_applicable:
        print(f"    {r.certificate.case_id} {r.intake.request_kind:<17} {by_id[r.certificate.case_id].request_text}")
    print(f"[{name}] intake AMBIGUOUS (defaulted to deletion): {len(ambiguous)}")
    for r in ambiguous:
        print(f"    {r.certificate.case_id} {by_id[r.certificate.case_id].request_text}")
    corroborated = [r for r in results if r.intake_evidence]
    print(f"[{name}] CLASSIFY_REQUEST non-deletion rows on the subject's resources: {len(corroborated)} case(s)")
    for r in corroborated:
        conflict = r.intake.authorizes_deletion is not False
        print(
            f"    {r.certificate.case_id} {list(r.intake_evidence)} text={r.intake.request_kind} "
            f"{'CONFLICT' if conflict else 'agrees'}"
        )
    fallback = [r.certificate.case_id for r in results if "fallback_no_provable_obligation" in r.certificate.flags]
    if fallback:
        print(f"[{name}] fallback NOT_APPLICABLE (no provable obligation): {fallback}")


def write_flags(path: Path, results: list[CaseResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        r.certificate.case_id: [f for f in r.certificate.flags if not f.startswith("perception_skipped:")]
        for r in sorted(results, key=lambda r: r.certificate.case_id)
    }
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(paths.ENV_FILE)
    mode = RunMode(args.mode)
    try:
        ctx = build_context(mode)
        evidence_times = load_evidence_times()
        print_canonical_systems(ctx)
        for source in SPLITS[args.split]:
            cases = load_requests(source)
            split_name = "test" if source == paths.DELETION_REQUESTS else "sample"
            results = run_split(cases, ctx, args.workers)
            print_split_summary(split_name, cases, results)
            write_flags(paths.RUNS_DIR / f"flags_{split_name}.json", results)
            rows = to_rows(results)
            validate_output(
                OUTPUT_COLUMNS,
                rows,
                expected_case_ids=[c.case_id for c in cases],
                audit_at={c.case_id: c.audit_at for c in cases},
                evidence_times=evidence_times,
                lineage=ctx.lineage,
            )
            target = paths.OUTPUT_CSV if source == paths.DELETION_REQUESTS else paths.RUNS_DIR / "sample_output.csv"
            write_rows(target, rows)
            print(f"wrote {len(rows)} rows -> {target}")
        ctx.ledger.write(paths.RUNS_DIR / f"usage_{args.split}_{mode.value}.json")
    except NotImplementedError as exc:
        print(f"skeleton: stage not implemented: {exc}. No output written.", file=sys.stderr)
        return 2
    except ContractViolation as exc:
        print(f"validation failed, no output written.\n{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
