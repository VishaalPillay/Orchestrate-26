"""Acceptance check for ResourceAttribution against the 32 public sample cases.

    python code/tools/check_attribution.py [--verbose]

Ownership/state split: ownership links come from evidence of any capture time
(the full FactStore), guarded by "the resource has a fact or activity row at or
before audit_at"; state and citations are strictly as_of(audit_at) and are not
exercised here. Resources whose ownership rests only on post-audit evidence are
listed with the anchor that satisfies the guardrail.

For each sample case it attributes resources at audit_at, compares the
resource_ids with those in sample_output.csv, and prints recall and precision
per case, every missing and every extra resource (extras with tier and
reasons; misses with whatever evidence exists for them), and the totals.
The case_17 decoy account_6523 must not be attributed: a violation fails the
run. Only participant-facing files are read; sample_output.csv is used as the
answer key here, never by the pipeline.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_summary import _NoPerception  # noqa: E402

from pcda import paths  # noqa: E402
from pcda.cases import load_requests  # noqa: E402
from pcda.facts import FactStore  # noqa: E402
from pcda.identity import IdentityClosure, ResourceAttribution  # noqa: E402
from pcda.ingest.base import IngestReport, read_manifest  # noqa: E402
from pcda.ingest.registry import build_adapters, ingest_all  # noqa: E402
from pcda.lineage import LineageGraph  # noqa: E402
from pcda.llm.client import RunMode  # noqa: E402

DECOYS = {"case_17": {"account_6523"}}


def gold_resources() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    with paths.SAMPLE_OUTPUT.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            steps = json.loads(row["steps_json"])
            out[row["case_id"]] = {s["resource_id"]: s["system_id"] for s in steps}
    return out


def anchor_at_audit(rid: str, full: FactStore, lineage: LineageGraph, case) -> str:
    """The first thing that existed for rid at or before audit_at (what the post-audit guardrail needs)."""
    for f in full.by_resource(rid):
        if f.observed_at <= case.audit_at:
            note = "" if f.captured_at <= case.audit_at else f", file captured {f.captured_at:%m-%d} after audit"
            return f"fact {f.fact_id} ({f.kind.value}, {f.evidence_id}, observed {f.observed_at:%m-%d %H:%M}{note})"
    for c in lineage.claims_for(rid, case.audit_at):
        return f"activity {c.activity_id} ({c.event_type} {c.event_time:%m-%d %H:%M})"
    return "NONE"


def explain_gap(rid: str, store: FactStore, lineage: LineageGraph, case) -> list[str]:
    lines: list[str] = []
    for f in store.by_resource(rid):
        lines.append(
            f"      fact {f.fact_id} {f.kind.value} src={f.attrs.get('src')} obs={f.observed_at:%Y-%m-%d %H:%M} "
            f"cap={f.captured_at:%m-%d %H:%M} ids={sorted(f.identifiers)[:3]}"
        )
    for c in lineage.claims:
        if c.resource_id == rid:
            lines.append(
                f"      claim {c.activity_id} {c.event_time:%Y-%m-%d %H:%M} {c.source_system}->{c.target_system} "
                f"{c.event_type} {c.reported_result} (reported_at offset "
                f"{(c.event_time - case.reported_at).total_seconds() / 60:+.1f} min)"
            )
    return lines or ["      (no fact and no activity row mentions this resource)"]


def main() -> int:
    verbose = "--verbose" in sys.argv
    report = IngestReport()
    full = ingest_all(
        read_manifest(), build_adapters(_NoPerception(), RunMode.DETERMINISTIC, report), report
    )
    closure = IdentityClosure.from_csvs(paths.USER_IDENTITIES, paths.USER_AUDIT_LOG)
    lineage = LineageGraph.from_csv(paths.SYSTEM_ACTIVITY)
    gold = gold_resources()

    print(f"identifier conflicts in closure: {len(closure.conflicts)}")
    header = f"{'case':<9}{'user':<10}{'gold':>5}{'attr':>5}{'hit':>5}{'miss':>5}{'extra':>6}{'recall':>8}{'prec':>7}"
    print(header)
    print("-" * len(header))
    tot_gold = tot_attr = tot_hit = 0
    details: list[str] = []
    failures: list[str] = []
    perfect = 0
    post_audit_owned: list[str] = []
    for case in load_requests(paths.SAMPLE_REQUESTS):
        user = closure.subject_of(case.email)
        store = full.as_of(case.audit_at)
        attributed, stale = (
            ResourceAttribution.for_case_detailed(case, user, closure, full, lineage) if user else ([], [])
        )
        got = {a.resource_id: a for a in attributed}
        want = gold[case.case_id]
        hit = set(got) & set(want)
        missing = sorted(set(want) - set(got))
        extra = sorted(set(got) - set(want))
        recall = len(hit) / len(want) if want else 1.0
        prec = len(hit) / len(got) if got else 1.0
        for a in attributed:
            if a.late_evidence_ids and not a.evidence_ids:
                anchor = anchor_at_audit(a.resource_id, full, lineage, case)
                post_audit_owned.append(
                    f"  {case.case_id} {a.system_id}/{a.resource_id} tier={a.tier.name} "
                    f"ownership from {list(a.late_evidence_ids)}; guardrail anchor: {anchor}; "
                    f"in gold: {a.resource_id in want}"
                )
        tot_gold += len(want)
        tot_attr += len(got)
        tot_hit += len(hit)
        perfect += not missing and not extra
        print(
            f"{case.case_id:<9}{(user or '-'):<10}{len(want):>5}{len(got):>5}{len(hit):>5}"
            f"{len(missing):>5}{len(extra):>6}{recall:>8.2f}{prec:>7.2f}"
        )
        for rid in missing:
            details.append(f"  {case.case_id} MISSING {want[rid]}/{rid}")
            details.extend(explain_gap(rid, full, lineage, case))
        for rid in extra:
            a = got[rid]
            details.append(
                f"  {case.case_id} EXTRA   {a.system_id}/{rid}  tier={a.tier.name} skeptic={a.needs_skeptic} "
                f"evidence={list(a.evidence_ids)}"
            )
            for reason in a.reasons:
                details.append(f"      {reason}")
        for a in stale:
            details.append(f"  {case.case_id} REJECTED {a.system_id}/{a.resource_id} -- {a.reasons[-1]}")
        for rid in DECOYS.get(case.case_id, ()):
            if rid in got:
                failures.append(f"{case.case_id}: decoy {rid} was attributed")
        if verbose:
            for a in attributed:
                mark = "ok " if a.resource_id in want else "?? "
                details.append(f"  {case.case_id} {mark}{a.system_id}/{a.resource_id} tier={a.tier.name} ev={list(a.evidence_ids)}")
    print("-" * len(header))
    recall = tot_hit / tot_gold
    prec = tot_hit / tot_attr
    print(
        f"{'TOTAL':<9}{'':<10}{tot_gold:>5}{tot_attr:>5}{tot_hit:>5}{tot_gold - tot_hit:>5}{tot_attr - tot_hit:>6}"
        f"{recall:>8.3f}{prec:>7.3f}"
    )
    print(f"cases with exact match: {perfect}/32")
    print("\nMisses and extras")
    print("\n".join(details) if details else "  none")
    print(f"\nOwnership established only by post-audit evidence ({len(post_audit_owned)}), with the guardrail anchor")
    print("\n".join(post_audit_owned) or "  none")
    print("\nDecoy check (case_17 account_6523):", "FAIL" if failures else "PASS")
    for f in failures:
        print("  ", f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
