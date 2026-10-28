"""Score predictions against the public sample answers.

    python code/evaluation/score.py [--pred PATH] [--gold PATH] [--diff]

Defaults: pred = code/.cache/runs/sample_output.csv (written by
`main.py --split sample|all`), gold = dataset/sample_output.csv. Gold is read
here only; the pipeline never sees it.

Reports:
  * case status accuracy and the status confusion;
  * step-level precision / recall / F1, matching steps as a multiset on
    (phase, system_id, resource_id, operation, state);
  * evidence_id accuracy over matched steps;
  * field-level accuracy, comparing step i of the prediction with step i of
    gold for every field;
  * exact-sequence matches (the whole ordered steps_json equal, evidence
    included) and order agreement of the matched steps;
  * steps lost, by gold operation and by why they were lost;
  * with --diff, a per-case diff.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pcda import paths
from pcda.contract import STEP_FIELDS

KEY_FIELDS = ("phase", "system_id", "resource_id", "operation", "state")


def load(path: Path) -> dict[str, tuple[str, list[dict]]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(fh)}


def key(step: dict) -> tuple[str, ...]:
    return tuple(str(step[f]) for f in KEY_FIELDS)


def match(pred: list[dict], gold: list[dict]) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Multiset match on KEY_FIELDS: (matched pairs, missing gold steps, extra predicted steps)."""
    pool: dict[tuple[str, ...], list[dict]] = {}
    for s in pred:
        pool.setdefault(key(s), []).append(s)
    pairs: list[tuple[dict, dict]] = []
    missing: list[dict] = []
    for g in gold:
        bucket = pool.get(key(g))
        if bucket:
            pairs.append((bucket.pop(0), g))
        else:
            missing.append(g)
    extra = [s for bucket in pool.values() for s in bucket]
    return pairs, missing, extra


def loss_reason(g: dict, pred: list[dict], pred_status: str) -> str:
    if pred_status == "NOT_APPLICABLE":
        return "case predicted NOT_APPLICABLE"
    same_res = [s for s in pred if s["resource_id"] == g["resource_id"]]
    if not same_res:
        return "resource not predicted"
    if all(s["system_id"] != g["system_id"] for s in same_res):
        return "system_id differs"
    if any(s["operation"] == g["operation"] for s in same_res):
        return "same operation, other field differs"
    return "resource predicted with other operations"


def fmt(step: dict) -> str:
    return (
        f"{step['phase'][:4]} {step['operation']:<26} {step['system_id']}/{step['resource_id']} "
        f"{step['state']} ev={step['evidence_id'] or '-'}"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Score predictions against sample_output.csv")
    p.add_argument("--pred", type=Path, default=paths.RUNS_DIR / "sample_output.csv")
    p.add_argument("--gold", type=Path, default=paths.SAMPLE_OUTPUT)
    p.add_argument("--diff", action="store_true", help="print a per-case diff")
    args = p.parse_args(argv)

    gold = load(args.gold)
    pred = load(args.pred)
    missing_cases = sorted(set(gold) - set(pred))
    if missing_cases:
        print(f"prediction lacks {len(missing_cases)} gold case(s): {missing_cases}")

    status_ok = 0
    confusion: Counter[tuple[str, str]] = Counter()
    tp = n_pred = n_gold = 0
    ev_ok = ev_total = 0
    field_ok: Counter[str] = Counter()
    positions = 0
    exact = 0
    order_ok = order_total = 0
    lost_by_op: Counter[str] = Counter()
    lost_by_reason: Counter[str] = Counter()
    extra_by_op: Counter[str] = Counter()
    diffs: list[str] = []

    for cid in sorted(gold):
        g_status, g_steps = gold[cid]
        p_status, p_steps = pred.get(cid, ("<missing>", []))
        confusion[(g_status, p_status)] += 1
        status_ok += g_status == p_status
        pairs, missing, extra = match(p_steps, g_steps)
        tp += len(pairs)
        n_pred += len(p_steps)
        n_gold += len(g_steps)
        for ps, gs in pairs:
            ev_total += 1
            ev_ok += ps["evidence_id"] == gs["evidence_id"]
        width = max(len(p_steps), len(g_steps))
        positions += width
        for i in range(width):
            for f in STEP_FIELDS:
                if i < len(p_steps) and i < len(g_steps) and p_steps[i][f] == g_steps[i][f]:
                    field_ok[f] += 1
        strip = lambda steps: [{f: s[f] for f in STEP_FIELDS} for s in steps]  # noqa: E731
        is_exact = g_status == p_status and strip(p_steps) == strip(g_steps)
        exact += is_exact
        # Order agreement: matched steps appear in the same relative order in both lists.
        if len(pairs) > 1:
            order_total += 1
            p_index = {id(ps): i for i, ps in enumerate(p_steps)}
            order_ok += [p_index[id(ps)] for ps, _ in pairs] == sorted(p_index[id(ps)] for ps, _ in pairs)
        for g in missing:
            lost_by_op[g["operation"]] += 1
            lost_by_reason[loss_reason(g, p_steps, p_status)] += 1
        for s in extra:
            extra_by_op[s["operation"]] += 1
        if args.diff and not is_exact:
            lines = [f"{cid}: gold {g_status} / pred {p_status}  matched {len(pairs)}/{len(g_steps)} extra {len(extra)}"]
            lines += [f"    - {fmt(g)}   [{loss_reason(g, p_steps, p_status)}]" for g in missing]
            lines += [f"    + {fmt(s)}" for s in extra]
            lines += [
                f"    ~ {ps['operation']} {ps['resource_id']} evidence {ps['evidence_id'] or '-'} != gold {gs['evidence_id'] or '-'}"
                for ps, gs in pairs
                if ps["evidence_id"] != gs["evidence_id"]
            ]
            diffs.append("\n".join(lines))

    n = len(gold)
    precision = tp / n_pred if n_pred else 0.0
    recall = tp / n_gold if n_gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    print(f"cases: {n}")
    print(f"status accuracy: {status_ok}/{n} = {status_ok / n:.3f}")
    print("status confusion (gold -> pred): " + ", ".join(f"{g}->{p}: {c}" for (g, p), c in sorted(confusion.items())))
    print(f"steps: gold {n_gold}, predicted {n_pred}, matched {tp}")
    print(f"step precision {precision:.3f}  recall {recall:.3f}  F1 {f1:.3f}")
    print(f"evidence_id accuracy on matched steps: {ev_ok}/{ev_total} = {ev_ok / ev_total if ev_total else 0:.3f}")
    print("field-level accuracy (position-aligned): " + ", ".join(
        f"{f} {field_ok[f] / positions:.3f}" for f in STEP_FIELDS
    ))
    print(f"exact-sequence matches: {exact}/{n} = {exact / n:.3f}")
    print(f"order agreement of matched steps: {order_ok}/{order_total} cases with >1 matched step")
    print("gold steps lost, by operation: " + (", ".join(f"{k} {v}" for k, v in lost_by_op.most_common()) or "none"))
    print("gold steps lost, by reason: " + (", ".join(f"{k} {v}" for k, v in lost_by_reason.most_common()) or "none"))
    print("extra predicted steps, by operation: " + (", ".join(f"{k} {v}" for k, v in extra_by_op.most_common()) or "none"))
    if args.diff:
        print("\nper-case diff (- gold step missing, + extra prediction, ~ evidence mismatch)")
        print("\n".join(diffs) if diffs else "  all cases exact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
