"""Run the skeptic over a split and record what happened.

Order of work: a first pass solves every case of both files without the
skeptic; that pass is the world the tools and the gate read. The queue for a
split is built over that split only, investigated until the split's call
budget is spent, and the accepted adjustments are stored on the context for
the final pass. Every investigation is written to code/.cache/runs/skeptic_<split>.jsonl
(verdict, cited ids, gate checks, calls, tokens; never a prompt or a key).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pcda import config, paths
from pcda.agents.skeptic import Adjustments, Budget, Investigation, LLMSkepticAgent, run_queue
from pcda.agents.skeptic_queue import build_queue
from pcda.agents.skeptic_tools import SkepticWorld
from pcda.cases import CaseRequest, load_requests
from pcda.pipeline import CaseResult, Context, run_case
from pcda.planner import Step


@dataclass
class SkepticRun:
    split: str
    queue_len: int
    budget: int
    budget_used: int
    unreached: int
    investigations: list[Investigation] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        c: Counter[str] = Counter()
        for inv in self.investigations:
            c["investigated"] += 1
            c[f"outcome_{inv.outcome}"] += 1
            g = inv.gate
            if inv.answer and inv.answer.verdict != "CONFIRM" and g is not None:
                c[f"{inv.answer.verdict}_{'accepted' if g.accepted else 'rejected'}"] += 1
                c["hallucinated_ids"] += int(g.hallucinated)
            c["cited_unseen"] += int(inv.cited_unseen)
            c["calls"] += inv.calls
            c["cached_calls"] += inv.cached_calls
            c["tool_calls"] += inv.tool_calls
            c["input_tokens"] += inv.input_tokens
            c["output_tokens"] += inv.output_tokens
        return dict(c)


def run_skeptic(
    ctx: Context, splits: dict[str, list[CaseRequest]], budgets: dict[str, int] | None = None
) -> dict[str, SkepticRun]:
    budgets = config.skeptic_budgets(budgets)
    every_case = load_requests(paths.SAMPLE_REQUESTS) + load_requests(paths.DELETION_REQUESTS)
    first_pass: dict[str, CaseResult] = {c.case_id: run_case(c, ctx, adjust=False) for c in every_case}
    world = SkepticWorld(
        store=ctx.store,
        lineage=ctx.lineage,
        closure=ctx.closure,
        templates=ctx.templates,
        cases={c.case_id: c for c in every_case},
        results=first_pass,
        canonical=ctx.canonical,
    )
    agent = LLMSkepticAgent(ctx.llm, world)
    runs: dict[str, SkepticRun] = {}
    ctx.skeptic_adjustments = {}
    ctx.skeptic_outcomes = {}
    for name, cases in splits.items():
        queue = build_queue(world, cases)
        budget = Budget(budgets[name])
        adjustments, invs, unreached = run_queue(agent, queue, budget)
        for cid, adj in adjustments.items():
            merged = ctx.skeptic_adjustments.setdefault(cid, Adjustments())
            merged.remove |= adj.remove
            merged.add.extend(adj.add)
        for inv in invs:
            g = inv.gate
            if inv.answer is None:
                continue
            label = inv.answer.verdict
            if label != "CONFIRM" and g is not None:
                label += "_ACCEPTED" if g.accepted else "_REJECTED"
            ctx.skeptic_outcomes[inv.target.key] = label
        runs[name] = SkepticRun(name, len(queue), budget.limit, budget.used, unreached, invs)
        # A replay must not overwrite the record of the live run it replays.
        suffix = "_replay" if ctx.mode.value == "replay" else ""
        write_records(paths.RUNS_DIR / f"skeptic_{name}{suffix}.jsonl", runs[name])
    return runs


def write_records(path: Path, run: SkepticRun) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        head = {
            "split": run.split, "queue": run.queue_len, "budget": run.budget, "budget_used": run.budget_used,
            "unreached": run.unreached, "counts": run.counts(),
        }
        fh.write(json.dumps({"summary": head}, ensure_ascii=False) + "\n")
        for inv in run.investigations:
            fh.write(json.dumps(inv.record(), ensure_ascii=False) + "\n")


# ---- audit and blast radius ---------------------------------------------------------


def _key(s: Step) -> tuple[str, str, str, str, str, str]:
    return (s.phase, s.system_id, s.resource_id, s.operation, s.state, s.evidence_id)


def diff_steps(before: list[Step], after: list[Step]) -> tuple[list[Step], list[Step]]:
    """(added, removed) as multisets; step numbers are ignored."""
    b, a = Counter(map(_key, before)), Counter(map(_key, after))
    added = [s for s in after if (a - b)[_key(s)] > 0]
    removed = [s for s in before if (b - a)[_key(s)] > 0]
    return added, removed


@dataclass(frozen=True)
class Blast:
    verdict_changes: tuple[tuple[str, str, str], ...]
    step_changes: int  # steps added + steps removed, over all cases
    cases_changed: tuple[str, ...]


def exceeds_blast_limit(blast: Blast, max_verdicts: int, max_steps: int) -> bool:
    """True when the skeptic changed more verdicts or steps than may be written without review."""
    return len(blast.verdict_changes) > max_verdicts or blast.step_changes > max_steps


def blast_radius(before: list[CaseResult], after: list[CaseResult]) -> Blast:
    verdicts: list[tuple[str, str, str]] = []
    steps = 0
    changed: list[str] = []
    for b, a in zip(before, after, strict=True):
        cid = b.certificate.case_id
        assert cid == a.certificate.case_id
        added, removed = diff_steps(b.steps, a.steps)
        if b.certificate.verdict != a.certificate.verdict:
            verdicts.append((cid, b.certificate.verdict.value, a.certificate.verdict.value))
        if added or removed or b.certificate.verdict != a.certificate.verdict:
            changed.append(cid)
        steps += len(added) + len(removed)
    return Blast(tuple(verdicts), steps, tuple(changed))


def _fmt(s: Step) -> str:
    return f"{s.operation} {s.system_id}/{s.resource_id} ev={s.evidence_id or '-'}"


def write_audit(
    path: Path, run: SkepticRun, before: list[CaseResult], after: list[CaseResult], blast: Blast
) -> None:
    """Markdown audit of every accepted adjustment, plus the outcome of every other investigation."""
    b_by = {r.certificate.case_id: r for r in before}
    a_by = {r.certificate.case_id: r for r in after}
    lines = [
        f"# Skeptic audit: {run.split}",
        "",
        f"Queue {run.queue_len} targets; {len(run.investigations)} investigated; {run.unreached} never reached; "
        f"{run.budget_used}/{run.budget} model calls.",
        f"Blast radius: {len(blast.verdict_changes)} verdict change(s), {blast.step_changes} step(s) added or removed, "
        f"{len(blast.cases_changed)} case(s) changed.",
        "",
        "## Accepted adjustments",
        "",
        "| case | action | resource | cited | gate checks passed | verdict before -> after | steps removed | steps added |",
        "|---|---|---|---|---|---|---|---|",
    ]
    accepted = [i for i in run.investigations if i.gate is not None and i.gate.accepted]
    for inv in accepted:
        g, cid = inv.gate, inv.target.case_id
        assert g is not None
        b, a = b_by[cid], a_by[cid]
        added, removed = diff_steps(b.steps, a.steps)
        lines.append(
            f"| {cid} | {g.action} | {g.resource_id} | {g.cited} | {'; '.join(g.checks)} | "
            f"{b.certificate.verdict.value} -> {a.certificate.verdict.value} | "
            f"{'<br>'.join(_fmt(s) for s in removed) or '-'} | {'<br>'.join(_fmt(s) for s in added) or '-'} |"
        )
    if not accepted:
        lines.append("| (none) | | | | | | | |")
    lines += ["", "## Every other investigation", "", "| case | target | outcome | answer | gate result |", "|---|---|---|---|---|"]
    for inv in run.investigations:
        if inv in accepted:
            continue
        ans, g = inv.answer, inv.gate
        if ans is None:
            what = "-"
        else:
            kind = f"/{ans.kind}" if ans.kind else ""
            what = f"{ans.verdict}{kind} cite={ans.fact_id or '-'} {ans.reason[:80]}"
        if g is None or ans is None or ans.verdict == "CONFIRM":
            res = "-"
        else:
            res = f"rejected: {g.reject_reason}" + (" (hallucinated id)" if g.hallucinated else "")
        lines.append(f"| {inv.target.case_id} | {inv.target.kind} {inv.target.resource_id or ''} | {inv.outcome} | {what} | {res} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
