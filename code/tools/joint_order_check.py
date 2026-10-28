"""Order independence of the forced activity assignment.

    python code/tools/joint_order_check.py

Builds every joint pass over all 550 requests in file order and in three
shuffled orders, and checks that assignments, refusals and refused holds are
identical. Deterministic mode, no provider calls.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from pcda import paths
from pcda.cases import load_requests
from pcda.identity import JointActivityAttribution
from pcda.llm.client import RunMode
from pcda.pipeline import JOINT_PASSES, build_context

SEEDS = (11, 2026, 90210)


def snapshot(j: JointActivityAttribution) -> tuple:
    return (
        tuple(sorted((cid, a.resource_id, a.evidence_ids) for cid, a in j.assigned.items())),
        tuple(sorted(j.rejected.items())),
        tuple(sorted(j.refused_holds.items())),
    )


def main() -> int:
    load_dotenv(paths.ENV_FILE)  # the perception cache is keyed on PERCEPTION_MODEL
    ctx = build_context(RunMode.DETERMINISTIC)
    every = load_requests(paths.SAMPLE_REQUESTS) + load_requests(paths.DELETION_REQUESTS)
    ok = True
    for event_type, system, fallback_only in JOINT_PASSES:
        def build(cases: list, event_type: str = event_type, system: str = system) -> JointActivityAttribution:
            return JointActivityAttribution.build(
                cases, ctx.closure, ctx.store, ctx.lineage, event_type=event_type, target_system=system
            )
        base = snapshot(build(every))
        print(f"{system}{' (fallback only)' if fallback_only else ''}: assigned {len(base[0])}, "
              f"rejected {len(base[1])}, refused holds {len(base[2])}")
        for seed in SEEDS:
            order = every[:]
            random.Random(seed).shuffle(order)
            same = snapshot(build(order)) == base
            ok &= same
            print(f"  seed {seed}: {'identical' if same else 'DIFFERENT'}")
    print("order independent" if ok else "ORDER DEPENDENT")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
