"""Evaluation report: metrics on the public samples and on the custom overlay cases.

    python code/evaluation/main.py              # print metrics for samples and custom cases
    python code/evaluation/main.py --write      # also rewrite the "Metrics" and
                                                # "Custom evaluation cases" sections of
                                                # code/evaluation/evaluation_report.md

Samples are read from code/.cache/runs/sample_output.csv, so run
`python code/main.py --mode deterministic --split all` first. Custom cases are
re-run from scratch (code/evaluation/custom_cases.py).

Metrics, for each set:
  * case-level status accuracy;
  * step-level precision / recall / F1, multiset match on
    (phase, system_id, resource_id, operation, state);
  * field-level accuracy per field, position-aligned (step i against step i);
  * sequence accuracy: exact ordered steps_json (all fields), and the mean
    normalized edit distance between predicted and gold step sequences
    (Levenshtein over whole steps / max length; 0 = identical);
  * a per-case diff.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import custom_cases  # noqa: E402
from score import fmt, match  # noqa: E402

from pcda import paths  # noqa: E402

FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")
REPORT = Path(__file__).resolve().parent / "evaluation_report.md"


@dataclass
class Pair:
    case_id: str
    label: str
    gold_status: str
    gold_steps: list[dict]
    pred_status: str
    pred_steps: list[dict]


@dataclass
class Metrics:
    n: int = 0
    status_ok: int = 0
    tp: int = 0
    n_pred: int = 0
    n_gold: int = 0
    field_ok: dict[str, int] = field(default_factory=lambda: {f: 0 for f in FIELDS})
    positions: int = 0
    exact: int = 0
    edit_sum: float = 0.0
    diffs: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.tp / self.n_pred if self.n_pred else 1.0

    @property
    def recall(self) -> float:
        return self.tp / self.n_gold if self.n_gold else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def _step_tuple(s: dict) -> tuple[str, ...]:
    return tuple(str(s[f]) for f in FIELDS)


def edit_distance(a: list[tuple], b: list[tuple]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, start=1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def evaluate(pairs: list[Pair]) -> Metrics:
    m = Metrics()
    for p in pairs:
        m.n += 1
        m.status_ok += p.gold_status == p.pred_status
        matched, missing, extra = match(p.pred_steps, p.gold_steps)
        m.tp += len(matched)
        m.n_pred += len(p.pred_steps)
        m.n_gold += len(p.gold_steps)
        width = max(len(p.pred_steps), len(p.gold_steps))
        m.positions += width
        for i in range(min(len(p.pred_steps), len(p.gold_steps))):
            for f in FIELDS:
                m.field_ok[f] += p.pred_steps[i][f] == p.gold_steps[i][f]
        a, b = [_step_tuple(s) for s in p.pred_steps], [_step_tuple(s) for s in p.gold_steps]
        exact = p.gold_status == p.pred_status and a == b
        m.exact += exact
        m.edit_sum += edit_distance(a, b) / width if width else 0.0
        if not exact:
            lines = [f"{p.label}: gold {p.gold_status} / pred {p.pred_status}, matched {len(matched)}/{len(p.gold_steps)}, extra {len(extra)}"]
            lines += [f"    - {fmt(g)}" for g in missing]
            lines += [f"    + {fmt(s)}" for s in extra]
            lines += [
                f"    ~ {ps['operation']} {ps['resource_id']} evidence {ps['evidence_id'] or '-'} != gold {gs['evidence_id'] or '-'}"
                for ps, gs in matched if ps["evidence_id"] != gs["evidence_id"]
            ]
            m.diffs.append("\n".join(lines))
    return m


def sample_pairs() -> list[Pair]:
    def load(path: Path) -> dict[str, tuple[str, list[dict]]]:
        with path.open(encoding="utf-8", newline="") as fh:
            return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(fh)}

    gold, pred = load(paths.SAMPLE_OUTPUT), load(paths.RUNS_DIR / "sample_output.csv")
    return [Pair(c, c, gold[c][0], gold[c][1], *pred.get(c, ("<missing>", []))) for c in sorted(gold)]


def custom_pairs() -> tuple[list[Pair], list[custom_cases.CustomResult]]:
    results = custom_cases.run_all()
    pairs = [
        Pair(r.case.case_id, r.case.name, r.case.expected_status, r.expected_rows, r.status, r.steps)
        for r in results
    ]
    return pairs, results


def metrics_table(rows: list[tuple[str, Metrics]]) -> str:
    out = ["| Metric | " + " | ".join(name for name, _ in rows) + " |", "|---|" + "---|" * len(rows)]

    def line(label: str, fn) -> None:
        out.append(f"| {label} | " + " | ".join(fn(m) for _, m in rows) + " |")

    line("Cases", lambda m: str(m.n))
    line("Status accuracy", lambda m: f"{m.status_ok}/{m.n} ({m.status_ok / m.n:.3f})")
    line("Steps (gold / predicted / matched)", lambda m: f"{m.n_gold} / {m.n_pred} / {m.tp}")
    line("Step precision / recall / F1", lambda m: f"{m.precision:.3f} / {m.recall:.3f} / {m.f1:.3f}")
    for f in FIELDS:
        line(f"Field accuracy: {f}", lambda m, f=f: f"{m.field_ok[f] / m.positions:.3f}" if m.positions else "n/a")
    line("Sequence accuracy (exact ordered steps)", lambda m: f"{m.exact}/{m.n} ({m.exact / m.n:.3f})")
    line("Mean normalized edit distance", lambda m: f"{m.edit_sum / m.n:.3f}")
    return "\n".join(out)


def render(sample: Metrics, custom: Metrics, results: list[custom_cases.CustomResult]) -> tuple[str, str]:
    metrics_md = "\n".join([
        "## Metrics",
        "",
        "Produced by `python code/evaluation/main.py --write` after `python code/main.py --mode deterministic --split all`.",
        "Steps are matched as a multiset on (phase, system_id, resource_id, operation, state). Field accuracy compares",
        "step i of the prediction with step i of gold, over the longer of the two lists, so a missing step costs every field",
        "after it. Edit distance is Levenshtein over whole steps (all six fields), divided by the longer length.",
        "",
        metrics_table([("Samples (32)", sample), (f"Custom overlays ({custom.n})", custom)]),
        "",
        "### Per-case diff: samples",
        "",
        "```text",
        "\n".join(sample.diffs) if sample.diffs else "all sample cases exact",
        "```",
        "",
        "### Per-case diff: custom overlays",
        "",
        "```text",
        "\n".join(custom.diffs) if custom.diffs else "all custom cases exact",
        "```",
        "",
    ])
    lines = [
        "## Custom evaluation cases",
        "",
        "Each case copies `dataset/` to a temporary directory, applies one targeted mutation, and runs the real pipeline on",
        "one case (`main.py --dataset-dir <overlay> --case <id> --out <file>`). The expected verdict and steps are literal",
        "in `code/evaluation/custom_cases.py`. Base cases are public samples the pipeline already matches exactly, so an",
        "unchanged expectation equals that sample's published answer. Run them with `python code/evaluation/custom_cases.py`.",
        "",
        "| # | Base | Mutation | Tests | Why | Expected | Result |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        c = r.case
        exp = f"{c.expected_status}, {len(c.expected_steps)} steps" + (
            f"; never {', '.join(c.must_not_contain)}" if c.must_not_contain else "")
        lines.append(
            f"| {c.name} | {c.case_id} | {c.mutation} | {c.tests} | {c.why} | {exp} | "
            f"{'pass' if r.passed else 'FAIL'} ({r.status}) |"
        )
    lines += [
        "",
        "Every mutation was confirmed to reach the fact store, so no pass is vacuous:",
        "- case 2's post-audit ABSENCE is ingested and then excluded by `as_of(audit_at)`;",
        "- case 3 leaves only the replica row of `search_1007`;",
        "- case 4's `search_9999` exists with an email that resolves to no user;",
        "- case 5b's uppercased key normalizes to `user_10`.",
        "",
    ]
    return metrics_md, "\n".join(lines)


def write_sections(metrics_md: str, custom_md: str) -> None:
    text = REPORT.read_text(encoding="utf-8")
    for title, body in (("## Metrics", metrics_md), ("## Custom evaluation cases", custom_md)):
        pattern = re.compile(rf"^{re.escape(title)}\n.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
        if pattern.search(text):
            text = pattern.sub(lambda _: body.rstrip("\n") + "\n\n", text)
        else:
            text = text.rstrip("\n") + "\n\n" + body
    REPORT.write_text(text.rstrip("\n") + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="rewrite the report sections")
    args = ap.parse_args(argv)
    sample = evaluate(sample_pairs())
    cpairs, results = custom_pairs()
    custom = evaluate(cpairs)
    metrics_md, custom_md = render(sample, custom, results)
    print(metrics_md)
    print(custom_md)
    if args.write:
        write_sections(metrics_md, custom_md)
        print(f"updated {REPORT}")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
