"""Print raw rows behind randomly drawn new account steps (seeded), for manual review.

    python code/tools/clock_spotcheck.py BEFORE.csv AFTER.csv [--n 10] [--seed 20261025]

For each drawn case: the request times, the picked account row, every other
status-matched account row inside the learned band with its phase test, and the
case's identifier-attributed run rows with their offsets from the pick.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from pcda import paths
from pcda.cases import load_requests
from pcda.identity import REPORTED_STATUS_RESULT, ResourceAttribution, normalize_email
from pcda.llm.client import RunMode
from pcda.pipeline import build_context
from pcda.runclock import ACCOUNT_SYSTEM, deletion_worker_rows, template


def accounts(path: Path, system: str = ACCOUNT_SYSTEM) -> dict[str, set[str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return {
            r["case_id"]: {s["resource_id"] for s in json.loads(r["steps_json"]) if s["system_id"] == system}
            for r in csv.DictReader(fh)
        }


def analytics_case(ctx, case, user, rid, worker) -> None:
    anchor = ctx.anchors[case.case_id]
    pick = next(r for r in worker[rid] if r.event_type == "ANONYMIZE_RECORD" and case.requested_at <= r.event_time <= case.audit_at)
    print(f"{case.case_id} ({user}) {case.reported_status} reported_at {case.reported_at:%m-%d %H:%M:%S}")
    print(f"  account row {anchor.activity_id} {anchor.resource_id} {anchor.event_time:%m-%d %H:%M:%S} {anchor.reported_result}")
    d = (pick.event_time - anchor.event_time).total_seconds()
    print(f"  PICK {pick.activity_id} {rid} {pick.event_time:%H:%M:%S} {pick.reported_result} +{d / 60:.1f} min residue {int(d) % ctx.clock.period_s}"
          f" | {template(pick.details)[:40]}")
    near = [r for r in ctx.lineage.claims
            if r.source_system == "deletion_worker" and r.target_system == "analytics_platform" and r.event_type == "ANONYMIZE_RECORD"
            and abs((r.event_time - anchor.event_time).total_seconds()) < 4 * 3600 and r.activity_id != pick.activity_id]
    for r in near:
        dd = (r.event_time - anchor.event_time).total_seconds()
        print(f"  other analytics row in +-4h: {r.activity_id} {r.resource_id} {dd / 60:+.1f} min residue {int(dd) % ctx.clock.period_s} "
              f"fits={ctx.clock.fits(anchor, r)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20261025)
    ap.add_argument("--system", default=ACCOUNT_SYSTEM, choices=[ACCOUNT_SYSTEM, "analytics_platform"])
    args = ap.parse_args()
    before, after = accounts(args.before, args.system), accounts(args.after, args.system)
    new = sorted(c for c in after if after[c] - before.get(c, set()))
    drawn = sorted(random.Random(args.seed).sample(new, min(args.n, len(new))))
    load_dotenv(paths.ENV_FILE)
    ctx = build_context(RunMode.DETERMINISTIC)
    cases = {c.case_id: c for c in load_requests(paths.DELETION_REQUESTS)}
    worker = deletion_worker_rows(ctx.lineage)
    print(f"{len(new)} cases gained a {args.system} step; drawing {len(drawn)} (seed {args.seed})\n")
    for cid in drawn:
        case = cases[cid]
        user = ctx.closure.subject_of(normalize_email(case.email))
        if args.system != ACCOUNT_SYSTEM:
            analytics_case(ctx, case, user, sorted(after[cid] - before.get(cid, set()))[0], worker)
            continue
        pick_id = sorted(after[cid] - before.get(cid, set()))[0]
        pick = next(r for r in worker[pick_id] if r.event_type == "DELETE_RECORD" and r.target_system == ACCOUNT_SYSTEM)
        print(f"{cid} ({user}) {case.reported_status} reported_at {case.reported_at:%m-%d %H:%M:%S} requested_at {case.requested_at:%m-%d %H:%M}")
        print(f"  PICK  {pick.activity_id} {pick.resource_id} {pick.event_time:%m-%d %H:%M:%S} {pick.reported_result} "
              f"({(pick.event_time - case.reported_at).total_seconds() / 60:+.1f} min)")
        want = REPORTED_STATUS_RESULT[case.reported_status]
        att = ctx.joint[0].assigned.get(cid)
        print(f"  tier  {att.tier.name if att else 'identifier'}")
        ident = [a for a in ResourceAttribution.for_case(case, user, ctx.closure, ctx.store, ctx.lineage) if not a.needs_skeptic]
        run = [r for a in ident for r in worker.get(a.resource_id, []) if r.event_time > pick.event_time]
        for r in sorted(run, key=lambda r: r.event_time):
            d = (r.event_time - pick.event_time).total_seconds()
            print(f"  run row {r.activity_id} {r.resource_id} {template(r.details)[:34]:<34} +{d:.0f} s (residue {int(d) % ctx.clock.period_s})")
        print(f"  pick phase-consistent: {ctx.clock.consistent(pick, run)}")
        for r in ctx.lineage.claims:
            if (r.event_type == "DELETE_RECORD" and r.target_system == ACCOUNT_SYSTEM and r.source_system == "deletion_worker"
                    and r.reported_result == want and case.requested_at <= r.event_time <= case.audit_at
                    and ctx.clock.in_band(r, case) and r.activity_id != pick.activity_id):
                holder = [c for c, accs in after.items() if r.resource_id in accs and c != cid]
                why = "phase-inconsistent" if not ctx.clock.consistent(r, run) else (f"held by {holder}" if holder else "free (not forced away)")
                print(f"  other in band: {r.activity_id} {r.resource_id} {r.event_time:%H:%M:%S} ({(r.event_time - case.reported_at).total_seconds() / 60:+.1f} min) -> {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
