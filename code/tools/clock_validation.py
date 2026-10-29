"""Label-free validation of the run clock and the matching rule. No gold is read.

    python code/tools/clock_validation.py

Uses only the evaluation requests whose account is attributed by identifiers
(pinned runs): their account is known without any activity row, so it is an
answer key the activity-based linking never saw.

  1. Held-out clock: learn the clock on the even-numbered pinned runs, check the
     odd-numbered ones (and the reverse). A pinned run's own account row must
     pass the band and the phase test; the number of other status-matched rows
     that also pass shows how selective the test is.
  2. Hide-and-force: take every pinned request, hide its identifier-attributed
     account, run the real account pass (candidates, ownership exclusion, phase,
     matching) and compare what it forces with the known account.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

import pcda.identity as identity
from pcda import paths
from pcda.cases import load_requests
from pcda.identity import REPORTED_STATUS_RESULT, JointActivityAttribution
from pcda.llm.client import RunMode
from pcda.pipeline import build_context
from pcda.runclock import ACCOUNT_SYSTEM, deletion_worker_rows, learn, pinned_runs


def number(case_id: str) -> int:
    return int(case_id.split("_")[1])


def held_out(ctx, test, runs) -> None:
    worker = deletion_worker_rows(ctx.lineage)
    account_rows = [
        c for rows in worker.values() for c in rows if c.event_type == "DELETE_RECORD" and c.target_system == ACCOUNT_SYSTEM
    ]
    for label, learn_on, check_on in (("even -> odd", 0, 1), ("odd -> even", 1, 0)):
        clock = learn([c for c in test if number(c.case_id) % 2 == learn_on], ctx.closure, ctx.store, ctx.lineage)
        passed = rejected = 0
        others: list[int] = []
        for case, anchor, later in runs:
            if number(case.case_id) % 2 != check_on:
                continue
            ok = clock.in_band(anchor, case) and clock.consistent(anchor, later)
            passed += ok
            rejected += not ok
            want = REPORTED_STATUS_RESULT[case.reported_status]
            others.append(
                sum(
                    1
                    for r in account_rows
                    if r.activity_id != anchor.activity_id
                    and r.reported_result == want
                    and case.requested_at <= r.event_time <= case.audit_at
                    and clock.in_band(r, case)
                    and clock.consistent(r, later)
                )
            )
        spread = Counter(min(n, 3) for n in others)
        print(f"clock learned {label}: true row passes {passed}, rejected {rejected}; "
              f"other rows that also pass (0/1/2/3+): {[spread[k] for k in range(4)]}")


def hide_and_force(ctx, test, runs) -> None:
    pinned = {case.case_id: anchor.resource_id for case, anchor, _ in runs}
    real = identity._partition

    def hidden(cases, closure, store, lineage, target_system):
        part = real(cases, closure, store, lineage, target_system)
        for cid, rid in pinned.items():
            part.strong.get(rid, set()).discard(part.subject_of.get(cid))
            part.identified[cid] = frozenset(r for r in part.identified[cid] if r != rid)
            if cid not in part.open_cases and cid in part.subject_of:
                case = next(c for c in cases if c.case_id == cid)
                part.open_cases[cid] = (case, part.subject_of[cid])
                part.held.pop(cid, None)
        return part

    identity._partition = hidden
    try:
        every = load_requests(paths.SAMPLE_REQUESTS) + test
        result = JointActivityAttribution.build(every, ctx.closure, ctx.store, ctx.lineage, clock=ctx.clock)
    finally:
        identity._partition = real
    tally: Counter[str] = Counter()
    for cid, rid in pinned.items():
        got = result.assigned.get(cid)
        tally["correct" if got and got.resource_id == rid else "wrong" if got else "abstained"] += 1
    print(f"hide-and-force over {len(pinned)} pinned requests: {dict(tally)}")


def main() -> int:
    load_dotenv(paths.ENV_FILE)
    ctx = build_context(RunMode.DETERMINISTIC)
    test = load_requests(paths.DELETION_REQUESTS)
    runs = pinned_runs(test, ctx.closure, ctx.store, ctx.lineage)
    print("\n".join(ctx.clock.describe()))
    held_out(ctx, test, runs)
    hide_and_force(ctx, test, runs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
