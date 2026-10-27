"""Would "nearest row to reported_at" pick the right account? Validated without gold.

    python code/tools/rank_validation.py

For every request whose account is already forced (an activity_temporal pick
that survived exclusivity, or an activity_joint assignment), hide the pick and
rank the request's feasible deletion_worker DELETE_RECORD rows on
account_service by |event_time - reported_at|. Feasible means the joint pass's
own constraints, before any elimination: requested_at <= event_time <=
min(reported_at, audit_at), reported_result matches reported_status, and no
fact or identifier attribution ties the account to another user. Reports how
often rank 1 is the forced pick, by tier and by gap, and the rank-1/rank-2
time margins. Deterministic, no provider calls.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from pcda import paths  # noqa: E402
from pcda.cases import load_requests  # noqa: E402
from pcda.identity import (  # noqa: E402
    ACCOUNT_SYSTEM,
    REPORTED_STATUS_RESULT,
    LinkTier,
    ResourceAttribution,
    conflicting_owner,
    normalize_email,
)
from pcda.llm.client import RunMode  # noqa: E402
from pcda.pipeline import build_context  # noqa: E402

GAP_BINS = ((0, 15, "<=15 min"), (15, 60, "15-60 min"), (60, 180, "1-3 h"), (180, 10**9, ">3 h"))


def main() -> int:
    load_dotenv(paths.ENV_FILE)  # the perception cache is keyed on PERCEPTION_MODEL
    ctx = build_context(RunMode.DETERMINISTIC)
    joint = ctx.joint[0]
    every = load_requests(paths.SAMPLE_REQUESTS) + load_requests(paths.DELETION_REQUESTS)
    sample_ids = {c.case_id for c in load_requests(paths.SAMPLE_REQUESTS)}
    rows = [
        c for c in ctx.lineage.claims
        if c.source_system == "deletion_worker" and c.event_type == "DELETE_RECORD" and c.target_system == ACCOUNT_SYSTEM
    ]
    strong: dict[str, set[str]] = defaultdict(set)
    temporal: dict[str, str] = {}
    for case in every:
        user = ctx.closure.subject_of(normalize_email(case.email))
        if user is None:
            continue
        accs = [a for a in ResourceAttribution.for_case(case, user, ctx.closure, ctx.store, ctx.lineage)
                if a.system_id == ACCOUNT_SYSTEM]
        for a in accs:
            if a.tier < LinkTier.ACTIVITY_TEMPORAL:
                strong[a.resource_id].add(user)
        if accs and all(a.tier == LinkTier.ACTIVITY_TEMPORAL for a in accs) and case.case_id not in joint.refused_holds:
            if accs[0].resource_id not in ctx.shared_picks:
                temporal[case.case_id] = accs[0].resource_id
    forced = {cid: ("temporal", rid) for cid, rid in temporal.items()}
    forced |= {cid: ("joint", a.resource_id) for cid, a in joint.assigned.items() if a.resource_id not in ctx.shared_picks}

    agree: Counter[tuple[str, str]] = Counter()
    total: Counter[tuple[str, str]] = Counter()
    by_gap: Counter[tuple[str, str, bool]] = Counter()
    margins: list[tuple[float, bool, str]] = []
    not_feasible: Counter[str] = Counter()
    for case in every:
        if case.case_id not in forced:
            continue
        tier, pick = forced[case.case_id]
        split = "sample" if case.case_id in sample_ids else "test"
        user = ctx.closure.subject_of(normalize_email(case.email))
        until = min(case.reported_at, case.audit_at)
        want = REPORTED_STATUS_RESULT.get(case.reported_status)
        feas = [
            c for c in rows
            if case.requested_at <= c.event_time <= until and c.reported_result == want
            and not (strong.get(c.resource_id, set()) - {user})
            and conflicting_owner(c.resource_id, user, ctx.closure, ctx.store) is None
        ]
        total[(tier, split)] += 1
        if pick not in {c.resource_id for c in feas}:
            not_feasible[tier] += 1
            continue
        ranked = sorted(feas, key=lambda c: (abs((c.event_time - case.reported_at).total_seconds()), c.activity_id))
        hit = ranked[0].resource_id == pick
        agree[(tier, split)] += hit
        gap = abs((next(c for c in ranked if c.resource_id == pick).event_time - case.reported_at).total_seconds()) / 60
        label = next(l for lo, hi, l in GAP_BINS if lo <= gap < hi)
        by_gap[(tier, label, hit)] += 1
        if len(ranked) > 1:
            d1 = abs((ranked[0].event_time - case.reported_at).total_seconds()) / 60
            d2 = abs((ranked[1].event_time - case.reported_at).total_seconds()) / 60
            margins.append((d2 - d1, hit, tier))

    print("agreement of rank 1 with the forced pick (feasible set before elimination)")
    for key in sorted(total):
        print(f"  {key[0]:<9}{key[1]:<7} {agree[key]}/{total[key]}"
              + (f" = {agree[key] / total[key]:.3f}" if total[key] else ""))
    for tier in ("temporal", "joint"):
        n = sum(v for (t, _), v in total.items() if t == tier)
        a = sum(v for (t, _), v in agree.items() if t == tier)
        print(f"  {tier} overall {a}/{n} = {a / n:.3f}; pick not in its own feasible set: {not_feasible[tier]}")
    n = sum(total.values()); a = sum(agree.values())
    print(f"  ALL {a}/{n} = {a / n:.3f}")
    print("by gap of the forced pick from reported_at (agree/total)")
    for tier in ("temporal", "joint"):
        for _, _, label in GAP_BINS:
            t = by_gap[(tier, label, True)] + by_gap[(tier, label, False)]
            if t:
                print(f"  {tier:<9}{label:<10} {by_gap[(tier, label, True)]}/{t}")
    print("rank-1/rank-2 margin in minutes (cases with 2+ feasible rows)")
    for tier in ("temporal", "joint"):
        for hit in (True, False):
            ms = sorted(m for m, h, t in margins if h == hit and t == tier)
            if ms:
                q = lambda p: ms[min(len(ms) - 1, int(p * len(ms)))]  # noqa: E731
                print(f"  {tier:<9}{'agree' if hit else 'disagree':<9} n={len(ms):<4} min {ms[0]:.0f} p25 {q(.25):.0f} "
                      f"median {q(.5):.0f} p75 {q(.75):.0f} max {ms[-1]:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
