"""Summarize skeptic runs from code/.cache/runs/skeptic_<split>.jsonl.

    python code/tools/skeptic_report.py [--split sample|test]

Prints, per split: queue and budget, outcomes (CONFIRM / CHALLENGE / PROPOSE),
what the gate did with each non-confirm (accepted, or rejected with its
reason), hallucinated and unseen citations, calls, tool calls and tokens, and
the targets by kind. Reads only the records the run wrote (no prompts, no keys).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pcda import paths  # noqa: E402


def report(split: str) -> None:
    path = paths.RUNS_DIR / f"skeptic_{split}.jsonl"
    if not path.exists():
        print(f"[{split}] no record at {path}")
        return
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    head, invs = rows[0]["summary"], rows[1:]
    print(f"== {split}: queue {head['queue']}, investigated {head['investigated'] if 'investigated' in head else len(invs)}, "
          f"never reached {head['unreached']}, calls {head['budget_used']}/{head['budget']}")
    by_kind = Counter(r["target_kind"] for r in invs)
    print("targets investigated by kind:", dict(sorted(by_kind.items())))
    print("outcomes:", dict(sorted(Counter(r["outcome"] for r in invs).items())))
    gate = Counter()
    for r in invs:
        a, g = r["answer"], r["gate"]
        if a and a["verdict"] != "CONFIRM" and g:
            gate[f"{a['verdict']} {'accepted' if g['accepted'] else 'rejected: ' + g['reject_reason']}"] += 1
    print("gate on non-confirm answers:", dict(sorted(gate.items())) or "none")
    print("hallucinated cited ids:", sum(1 for r in invs if r["gate"] and r["gate"]["hallucinated"]),
          "| cited but never shown by a tool:", sum(1 for r in invs if r["cited_unseen"]))
    calls = sum(r["calls"] for r in invs)
    print(f"model calls {calls} (cached {sum(r['cached_calls'] for r in invs)}), tool calls {sum(r['tool_calls'] for r in invs)}, "
          f"tokens in/out {sum(r['input_tokens'] for r in invs)}/{sum(r['output_tokens'] for r in invs)}, "
          f"models {sorted({m for r in invs for m in r['models']})}")
    for r in invs:
        if r["answer"] and r["answer"]["verdict"] != "CONFIRM":
            print("  ", r["case_id"], r["target_kind"], r["resource_id"], json.dumps(r["answer"]), json.dumps(r["gate"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["sample", "test"], action="append", default=[])
    args = ap.parse_args()
    for split in args.split or ["sample", "test"]:
        report(split)
    return 0


if __name__ == "__main__":
    sys.exit(main())
