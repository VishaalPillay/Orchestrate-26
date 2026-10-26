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
from pcda.agents.skeptic_run import SkepticRun, blast_radius, run_skeptic, write_audit
from pcda.cases import SPLIT_NAMES, CaseRequest, load_evidence_times, load_requests, split_sources
from pcda.contract import OUTPUT_COLUMNS
from pcda.llm.client import RunMode
from pcda.pipeline import CaseResult, Context, build_context, run_case
from pcda.planner import serialize_steps
from pcda.validator import ContractViolation, validate_output


BLAST_MAX_VERDICTS = 10
BLAST_MAX_STEPS = 25


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="pcda", description="Ghost Data Still Exists deletion audit")
    p.add_argument("--mode", choices=[m.value for m in RunMode], default=RunMode.DETERMINISTIC.value)
    p.add_argument("--split", choices=SPLIT_NAMES, default="sample")
    p.add_argument("--dataset-dir", type=Path, default=None,
                   help="read every dataset file from this directory (custom evaluation overlays)")
    p.add_argument("--case", action="append", default=[],
                   help="run only these case_ids (repeatable)")
    p.add_argument("--out", type=Path, default=None,
                   help="write rows here; required with --dataset-dir or --case, which never touch output.csv")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--skeptic", choices=["on", "off"], default="on",
                   help="the skeptic agent (live and replay only; deterministic mode never runs it)")
    p.add_argument("--skeptic-budget", action="append", default=[], metavar="SPLIT=N",
                   help="model-call budget per split (defaults: sample=60, test=120)")
    p.add_argument("--accept-skeptic-blast-radius", action="store_true",
                   help="write output.csv even when the skeptic changed more than %d verdicts or %d steps" % (
                       BLAST_MAX_VERDICTS, BLAST_MAX_STEPS))
    return p.parse_args(argv)


def run_split(cases: list[CaseRequest], ctx: Context, workers: int, adjust: bool = True) -> list[CaseResult]:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda c: run_case(c, ctx, adjust), cases))


def print_skeptic_summary(runs: dict[str, SkepticRun]) -> None:
    for name, run in runs.items():
        print(
            f"[skeptic {name}] queue {run.queue_len}, investigated {len(run.investigations)}, "
            f"never reached {run.unreached}, calls {run.budget_used}/{run.budget}"
        )
        print(f"[skeptic {name}] {json.dumps(run.counts(), sort_keys=True)}")


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
    overlay = args.dataset_dir is not None or bool(args.case)
    if overlay and args.out is None:
        print("--dataset-dir and --case need --out (they never write output.csv)", file=sys.stderr)
        return 2
    if args.dataset_dir is not None:
        paths.use_dataset(args.dataset_dir)
    try:
        ctx = build_context(mode)
        evidence_times = load_evidence_times()
        skeptic_runs: dict[str, SkepticRun] = {}
        if mode is not RunMode.DETERMINISTIC and args.skeptic == "on" and not overlay:
            to_run = {}
            for source in split_sources(args.split):
                to_run["test" if source == paths.DELETION_REQUESTS else "sample"] = load_requests(source)
            budgets = {k: int(v) for k, v in (b.split("=", 1) for b in args.skeptic_budget)}
            skeptic_runs = run_skeptic(ctx, to_run, budgets)
            print_skeptic_summary(skeptic_runs)
        print_canonical_systems(ctx)
        selected: list[CaseRequest] = []
        for source in split_sources(args.split):
            cases = load_requests(source)
            if args.case:
                cases = [c for c in cases if c.case_id in set(args.case)]
                selected.extend(cases)
                continue
            split_name = "test" if source == paths.DELETION_REQUESTS else "sample"
            results = run_split(cases, ctx, args.workers)
            print_split_summary(split_name, cases, results)
            blast = None
            if split_name in skeptic_runs:
                before = run_split(cases, ctx, args.workers, adjust=False)
                blast = blast_radius(before, results)
                audit_path = paths.RUNS_DIR / f"skeptic_audit_{split_name}.md"
                write_audit(audit_path, skeptic_runs[split_name], before, results, blast)
                print(
                    f"[skeptic {split_name}] blast radius: {len(blast.verdict_changes)} verdict change(s) "
                    f"{list(blast.verdict_changes)}, {blast.step_changes} step(s) added or removed, "
                    f"{len(blast.cases_changed)} case(s) changed; audit -> {audit_path}"
                )
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
            if (
                blast is not None
                and source == paths.DELETION_REQUESTS
                and (len(blast.verdict_changes) > BLAST_MAX_VERDICTS or blast.step_changes > BLAST_MAX_STEPS)
                and not args.accept_skeptic_blast_radius
            ):
                candidate = paths.RUNS_DIR / "skeptic_candidate_output.csv"
                write_rows(candidate, rows)
                print(
                    f"STOP: the skeptic would change more than {BLAST_MAX_VERDICTS} verdicts or {BLAST_MAX_STEPS} steps. "
                    f"output.csv was NOT written. Candidate: {candidate}; review the audit, then rerun with "
                    "--accept-skeptic-blast-radius or --skeptic off.",
                    file=sys.stderr,
                )
                return 3
            write_rows(target, rows)
            print(f"wrote {len(rows)} rows -> {target}")
        if args.case:
            missing = sorted(set(args.case) - {c.case_id for c in selected})
            if missing:
                print(f"unknown case_id(s): {missing}", file=sys.stderr)
                return 2
            results = run_split(selected, ctx, args.workers)
            rows = to_rows(results)
            validate_output(
                OUTPUT_COLUMNS,
                rows,
                expected_case_ids=[c.case_id for c in selected],
                audit_at={c.case_id: c.audit_at for c in selected},
                evidence_times=evidence_times,
                lineage=ctx.lineage,
            )
            write_rows(args.out, rows)
            write_flags(args.out.with_suffix(".flags.json"), results)
            print(f"wrote {len(rows)} rows -> {args.out}")
        if not overlay:
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
