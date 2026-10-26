"""Negative control: does the skeptic catch a pick that is wrong?

    python code/tools/skeptic_control.py [--mode live|replay] [--budget 45]

The public samples hold only correct picks, so a run on them can only show
that the skeptic does not break anything. Here every sample case that has an
activity pick gets the NEXT such case's pick instead (a real deletion_worker
row, plainly another request's), the agent and gate run on that corrupted
first pass, and the result is counted: a CHALLENGE the gate accepts is a
caught error, a CONFIRM is a miss. Nothing is written to output.csv; the
records go to code/.cache/runs/skeptic_control.jsonl.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from pcda import paths  # noqa: E402
from pcda.agents.skeptic import Answer, Budget, LLMSkepticAgent, Target  # noqa: E402
from pcda.agents.skeptic_gate import evaluate  # noqa: E402
from pcda.agents.skeptic_gate import CHALLENGEABLE  # noqa: E402
from pcda.agents.skeptic_tools import SkepticWorld  # noqa: E402
from pcda.cases import load_requests  # noqa: E402
from pcda.llm.client import RunMode  # noqa: E402
from pcda.pipeline import build_context, run_case  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["live", "replay"], default="live")
    ap.add_argument("--budget", type=int, default=45)
    args = ap.parse_args()
    load_dotenv(paths.ENV_FILE)
    ctx = build_context(RunMode(args.mode))
    every = load_requests(paths.SAMPLE_REQUESTS) + load_requests(paths.DELETION_REQUESTS)
    first = {c.case_id: run_case(c, ctx, adjust=False) for c in every}
    samples = [c.case_id for c in load_requests(paths.SAMPLE_REQUESTS)]
    holders = [
        (cid, a)
        for cid in samples
        for a in first[cid].attributions
        if a.tier in CHALLENGEABLE and a.system_id == "account_service"
    ]
    print(f"{len(holders)} sample cases hold an account pick; rotating them")
    swapped: dict[str, object] = dict(first)
    targets: list[Target] = []
    cases = {c.case_id: c for c in every}
    for i, (cid, old) in enumerate(holders):
        new = holders[(i + 1) % len(holders)][1]
        res = first[cid]
        obligations = tuple(
            dataclasses.replace(o, resource=new) if o.resource_id == old.resource_id else o
            for o in res.certificate.obligations
        )
        cert = dataclasses.replace(res.certificate, obligations=obligations)
        attrs = tuple(new if a.resource_id == old.resource_id else a for a in res.attributions)
        swapped[cid] = dataclasses.replace(res, certificate=cert, attributions=attrs)
        targets.append(
            Target(cid, "PICK", new.resource_id, 0, 0.0, f"activity pick {new.resource_id} read from a deletion_worker row")
        )
    world = SkepticWorld(
        store=ctx.store, lineage=ctx.lineage, closure=ctx.closure, templates=ctx.templates, cases=cases,
        results=swapped, canonical=ctx.canonical,  # type: ignore[arg-type]
    )
    # Oracle: would the gate accept the perfect challenge (the true holder of the row, the row itself)?
    reachable = 0
    for i, (cid, _old) in enumerate(holders):
        holder_case, new = holders[(i + 1) % len(holders)]
        g = evaluate(
            Answer("CHALLENGE", "OTHER_REQUEST", new.resource_id, new.evidence_ids[0], holder_case),
            Target(cid, "PICK", new.resource_id, 0, 0.0, "oracle"),
            world,
        )
        reachable += int(g.accepted)
        print(f"oracle {cid} {new.resource_id} <- {holder_case}: {'ACCEPT' if g.accepted else 'reject: ' + g.reject_reason}")
    print(f"gate-reachable perfect challenges: {reachable}/{len(holders)}")
    agent = LLMSkepticAgent(ctx.llm, world)
    budget = Budget(args.budget)
    out = paths.RUNS_DIR / "skeptic_control.jsonl"
    tally: Counter[str] = Counter()
    with out.open("w", encoding="utf-8") as fh:
        for t in targets:
            if budget.left <= 0:
                tally["not_reached"] += 1
                continue
            inv = agent.investigate(t, budget)
            if inv.outcome == "UNFINISHED":
                tally["not_reached"] += 1
                continue
            verdict = inv.outcome
            if inv.gate is not None and inv.answer is not None and inv.answer.verdict != "CONFIRM":
                verdict += "_ACCEPTED" if inv.gate.accepted else f"_REJECTED({inv.gate.reject_reason})"
            tally[verdict] += 1
            tally["hallucinated"] += int(bool(inv.gate and inv.gate.hallucinated))
            fh.write(json.dumps(inv.record(), ensure_ascii=False) + "\n")
            print(t.case_id, t.resource_id, verdict, inv.calls, "calls")
    print(dict(tally), f"calls {budget.used}/{budget.limit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
