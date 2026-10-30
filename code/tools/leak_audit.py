"""Test-side leak audit: every flag and silent rejection, grouped by type. No gold is read.

    python code/tools/leak_audit.py [--out code/.cache/runs/leak_audit.md]

Runs the deterministic pipeline over all 550 requests and collects, per case:
  * every certificate flag, grouped by prefix (certificate flags);
  * attribution rejections that are not flagged: stale and post-audit candidates;
  * ingest drops: perception records that failed grounding, and skipped images;
  * obligations left UNKNOWN, by category (what unknown_state hides).
Each type is labeled with the leak it signals: recall (a step is likely
missing) or precision (a step is likely wrong). Sample counts are shown next to
test counts, because a type the samples never exercise is unmeasured.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from pcda import paths
from pcda.cases import load_requests
from pcda.identity import ResourceAttribution, normalize_email
from pcda.llm.client import RunMode
from pcda.pipeline import build_context, run_case
from pcda.solver import ObligationStatus

# Flag prefix -> (leak, what it means). Unlisted prefixes are reported as "info".
LEAK: dict[str, tuple[str, str]] = {
    "unknown_state": ("recall", "an attributed resource whose state at audit_at is unprovable; dropped from COMPLETE steps"),
    "category_unknown": ("recall", "an attributed resource with no category; no obligation, no step"),
    "category_unknown_name": ("recall", "a record/table/event name the category map does not know"),
    "retained_missing": ("recall", "a record policy requires to be retained reads ABSENT"),
    "uncited": ("recall", "a COMPLETE obligation with no step emitted"),
    "joint_none": ("recall", "no activity row could be forced to the request"),
    "staleness_rejected": ("recall", "a candidate whose newest supporting observation precedes requested_at"),
    "post_audit_rejected": ("recall", "a candidate with no fact observed and no activity row at or before audit_at"),
    "perception_drop": ("recall", "a screenshot record dropped by grounding"),
    "perception_skipped": ("recall", "an image with no cached transcription"),
    "fallback_no_provable_obligation": ("recall", "nothing provable: written as INCOMPLETE with no steps"),
    "activity_pick_refused": ("recall if wrong", "an activity pick refused by exclusivity (precision guard)"),
    "category_conflict": ("precision", "two record names resolve to different categories; one was chosen"),
    "intake_ambiguous": ("precision", "request text defaulted to deletion"),
    "unproven_guard": ("precision", "a flow or archive with no evidenced guard state is treated as OPEN"),
    "skeptic_skipped": ("precision", "activity-derived picks that no skeptic reviewed"),
    "system_canonical": ("info", "a resource's system_id rewritten to its prefix owner"),
    "attribution_merged": ("info", "one resource attributed twice under different systems"),
}


def collect(split_file: Path, ctx) -> tuple[dict[str, list[tuple[str, str]]], Counter[str]]:
    by_type: dict[str, list[tuple[str, str]]] = defaultdict(list)
    unknown_by_cat: Counter[str] = Counter()
    for case in load_requests(split_file):
        result = run_case(case, ctx)
        for f in result.certificate.flags:
            by_type[f.split(":")[0]].append((case.case_id, f))
        for o in result.certificate.obligations:
            if o.status is ObligationStatus.UNKNOWN:
                unknown_by_cat[o.category] += 1
        subject = ctx.closure.subject_of(normalize_email(case.email))
        if subject is None:
            continue
        _, rejected = ResourceAttribution.for_case_detailed(case, subject, ctx.closure, ctx.store, ctx.lineage)
        for r in rejected:
            why = r.reasons[-1]
            if why.startswith("stale"):
                by_type["staleness_rejected"].append((case.case_id, f"{r.resource_id} ({r.system_id}): {why}"))
            elif why.startswith("post-audit"):
                by_type["post_audit_rejected"].append((case.case_id, f"{r.resource_id} ({r.system_id})"))
    return by_type, unknown_by_cat


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=paths.RUNS_DIR / "leak_audit.md")
    args = p.parse_args()
    load_dotenv(paths.ENV_FILE)  # the perception cache is keyed on PERCEPTION_MODEL
    ctx = build_context(RunMode.DETERMINISTIC)
    test, unknown_test = collect(paths.DELETION_REQUESTS, ctx)
    sample, unknown_sample = collect(paths.SAMPLE_REQUESTS, ctx)
    drops = [w for w in ctx.report.warnings if "dropped" in w]
    test["perception_drop"] = [("global", w) for w in drops]

    lines = ["# Leak audit (deterministic, no gold)", "",
             "| type | leak | test count | test cases | sample count | meaning |", "|---|---|---|---|---|---|"]
    for t in sorted(set(test) | set(sample), key=lambda t: (-len(test.get(t, [])), t)):
        leak, meaning = LEAK.get(t, ("info", ""))
        items = test.get(t, [])
        lines.append(
            f"| {t} | {leak} | {len(items)} | {len({c for c, _ in items})} | {len(sample.get(t, []))} | {meaning} |"
        )
    lines += ["", "## UNKNOWN obligations by category", "", "| category | test | sample |", "|---|---|---|"]
    for cat in sorted(set(unknown_test) | set(unknown_sample)):
        lines.append(f"| {cat} | {unknown_test[cat]} | {unknown_sample[cat]} |")
    lines += ["", "## Examples (first 3 test items per type)", ""]
    for t in sorted(test):
        lines.append(f"### {t}")
        lines += [f"- {cid}: `{f}`" for cid, f in sorted(test[t])[:3]]
        lines.append("")
    names = Counter(f.split(":", 2)[2] for _, f in test.get("category_unknown_name", []))
    lines += ["## Unknown category names (test)", ""] + [f"- `{n}`: {c}" for n, c in names.most_common()]
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[: lines.index("## Examples (first 3 test items per type)")]))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
