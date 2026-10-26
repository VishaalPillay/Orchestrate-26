"""Exhaustive gate sweep: what the gate would accept with no model at all.

    python code/tools/skeptic_sweep.py

The agent can only propose what the gate accepts, so the gate's reachable set
bounds the skeptic's possible effect. This tool runs the gate's own predicates
over every candidate in the stores, for every case of both files:

  * CHALLENGE / OTHER_SUBJECT: every fact (or catalog row) that mentions an
    activity pick and resolves to another user;
  * CHALLENGE / OTHER_REQUEST: every other request, for every activity pick;
  * PROPOSE / MISSED_LINK: every fact of the subject that mentions a resource
    the case does not hold.

Deterministic mode data only (replay cache); no provider is called.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from pcda import paths  # noqa: E402
from pcda.agents.skeptic import Adjustments, Answer, Target  # noqa: E402
from pcda.agents.skeptic_run import blast_radius, diff_steps  # noqa: E402
from pcda.agents.skeptic_gate import CHALLENGEABLE, evaluate  # noqa: E402
from pcda.agents.skeptic_tools import SkepticWorld, fact_mentions  # noqa: E402
from pcda.cases import load_requests  # noqa: E402
from pcda.llm.client import RunMode  # noqa: E402
from pcda.pipeline import build_context, run_case  # noqa: E402

TOKEN = re.compile(r"(?<![A-Za-z0-9_])([a-z][a-z_]*\d+)(?![0-9])")


def main() -> int:
    load_dotenv(paths.ENV_FILE)
    ctx = build_context(RunMode.REPLAY)
    every = load_requests(paths.SAMPLE_REQUESTS) + load_requests(paths.DELETION_REQUESTS)
    sample_ids = {c.case_id for c in load_requests(paths.SAMPLE_REQUESTS)}
    results = {c.case_id: run_case(c, ctx, adjust=False) for c in every}
    world = SkepticWorld(
        ctx.store, ctx.lineage, ctx.closure, ctx.templates, {c.case_id: c for c in every}, results, ctx.canonical
    )
    known = {f.resource_id for f in ctx.store if f.resource_id} | {c.resource_id for c in ctx.lineage.claims}
    claims_by_resource: dict[str, list[str]] = {}
    for c in ctx.lineage.claims:
        if c.event_type == "LINK_RESOURCE_IDENTITY":
            claims_by_resource.setdefault(c.resource_id, []).append(c.activity_id)
    tally: Counter[str] = Counter()
    accepted: list[str] = []
    removals: dict[str, Adjustments] = {}
    cited_for: dict[tuple[str, str], tuple[str, str]] = {}  # (case, resource) -> (first cited id, last gate check)
    link_types: Counter[str] = Counter()
    for case in every:
        part = "sample" if case.case_id in sample_ids else "test"
        res = results[case.case_id]
        for a in res.attributions:
            if a.tier not in CHALLENGEABLE:
                continue
            t = Target(case.case_id, "PICK", a.resource_id, 0, 0.0, "sweep")
            tally[f"{part} picks"] += 1
            hit = False
            for f in ctx.store:
                if f.resource_id and fact_mentions(f, a.resource_id):
                    g = evaluate(Answer("CHALLENGE", "OTHER_SUBJECT", a.resource_id, f.fact_id), t, world)
                    if g.accepted:
                        hit = True
                        removals.setdefault(case.case_id, Adjustments()).remove.add(a.resource_id)
                        cited_for.setdefault((case.case_id, a.resource_id), (f.fact_id, g.checks[-1]))
                        kind = (
                            "own record" if f.resource_id == a.resource_id
                            else "linked_resource" if f.attrs.get("linked_resource") == a.resource_id
                            else "co-mention"
                        )
                        link_types[f"{part} OTHER_SUBJECT via {kind}"] += 1
                        accepted.append(f"{case.case_id} CHALLENGE/OTHER_SUBJECT {a.resource_id} via {f.fact_id} ({kind}) {g.checks[-1]}")
            for aid in claims_by_resource.get(a.resource_id, []):
                g = evaluate(Answer("CHALLENGE", "OTHER_SUBJECT", a.resource_id, aid), t, world)
                if g.accepted:
                    hit = True
                    removals.setdefault(case.case_id, Adjustments()).remove.add(a.resource_id)
                    cited_for.setdefault((case.case_id, a.resource_id), (aid, g.checks[-1]))
                    link_types[f"{part} OTHER_SUBJECT via catalog row"] += 1
                    accepted.append(f"{case.case_id} CHALLENGE/OTHER_SUBJECT {a.resource_id} via {aid} {g.checks[-1]}")
            for evid in a.evidence_ids:
                for other in every:
                    if other.case_id == case.case_id:
                        continue
                    g = evaluate(Answer("CHALLENGE", "OTHER_REQUEST", a.resource_id, evid, other.case_id), t, world)
                    if g.accepted:
                        hit = True
                        removals.setdefault(case.case_id, Adjustments()).remove.add(a.resource_id)
                        cited_for.setdefault((case.case_id, a.resource_id), (evid, f"OTHER_REQUEST -> {other.case_id}"))
                        link_types[f"{part} OTHER_REQUEST"] += 1
                        accepted.append(f"{case.case_id} CHALLENGE/OTHER_REQUEST {a.resource_id} via {evid} -> {other.case_id}")
            tally[f"{part} picks the gate would remove"] += int(hit)
        subject = res.certificate.subject
        if subject is None:
            continue
        t = Target(case.case_id, "CONTESTED", None, 4, 0.0, "sweep")
        proposed: set[str] = set()
        for ident in sorted(ctx.closure.identifiers_of(subject)):
            for f in ctx.store.by_identifier(ident):
                cands = set(TOKEN.findall(" ".join(v for k, v in f.attrs.items() if k not in ("source_line", "src", "table"))))
                if f.attrs.get("linked_resource"):
                    cands.add(f.attrs["linked_resource"])
                for rid in sorted(cands & known):
                    g = evaluate(Answer("PROPOSE", "MISSED_LINK", rid, f.fact_id), t, world)
                    if g.accepted and rid not in proposed:
                        proposed.add(rid)
                        accepted.append(f"{case.case_id} PROPOSE/MISSED_LINK {rid} via {f.fact_id}")
        tally[f"{part} cases"] += 1
        tally[f"{part} cases with a reachable proposal"] += int(bool(proposed))
        tally[f"{part} reachable proposals"] += len(proposed)
    for k, v in sorted(tally.items()):
        print(f"{k}: {v}")
    print(f"gate-accepted claims in total: {len(accepted)}")
    for k, v in sorted(link_types.items()):
        print(f"{k}: {v}")
    # Upper bound: every gate-accepted removal applied, no model involved.
    ctx.skeptic_adjustments = removals
    for part, cases in (("sample", [c for c in every if c.case_id in sample_ids]), ("test", [c for c in every if c.case_id not in sample_ids])):
        before = [results[c.case_id] for c in cases]
        after = [run_case(c, ctx, adjust=True) for c in cases]
        blast = blast_radius(before, after)
        print(f"{part}: applying every reachable removal changes {len(blast.verdict_changes)} verdict(s), "
              f"{blast.step_changes} step(s), {len(blast.cases_changed)} case(s)")
        shown = 0
        for b, a in zip(before, after):
            added, removed = diff_steps(b.steps, a.steps)
            if (added or removed or b.certificate.verdict != a.certificate.verdict) and shown < 6:
                shown += 1
                print(f"   {b.certificate.case_id} {b.certificate.verdict.value}->{a.certificate.verdict.value} "
                      f"-{len(removed)} +{len(added)}")
    lines = [
        "# Skeptic sweep audit: every gate-reachable removal on test (no model)", "",
        "| case | pick removed | tier | first cited row | gate check | verdict before -> after | steps removed | steps added |",
        "|---|---|---|---|---|---|---|---|",
    ]
    after_by = {c.case_id: run_case(c, ctx, adjust=True) for c in every if c.case_id in removals}
    for cid in sorted(removals):
        if cid in sample_ids:
            continue
        b, a = results[cid], after_by[cid]
        added, removed = diff_steps(b.steps, a.steps)
        fmt = lambda st: f"{st.operation} {st.system_id}/{st.resource_id} ev={st.evidence_id or '-'}"
        for rid in sorted(removals[cid].remove):
            tier = next(x.tier.name for x in b.attributions if x.resource_id == rid)
            cited, check = cited_for[(cid, rid)]
            lines.append(
                f"| {cid} | {rid} | {tier} | {cited} | {check} | {b.certificate.verdict.value} -> {a.certificate.verdict.value} | "
                f"{'<br>'.join(map(fmt, removed)) or '-'} | {'<br>'.join(map(fmt, added)) or '-'} |"
            )
    (paths.RUNS_DIR / "skeptic_audit_sweep.md").write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    for line in accepted:
        print("  ", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
