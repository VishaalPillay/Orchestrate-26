"""Diff two output files: verdict flips, step changes, and resource changes per system.

    python code/tools/output_diff.py BEFORE.csv AFTER.csv [--system account_service ...]

A resource change is the set of resource_ids a case's steps name on a system,
before and after: added (none -> some), removed (some -> none), changed.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


def load(path: Path) -> dict[str, tuple[str, list[dict]]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(fh)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p.add_argument("--system", action="append", default=[])
    args = p.parse_args()
    a, b = load(args.before), load(args.after)
    flips: Counter[tuple[str, str]] = Counter()
    changed_rows = 0
    removed: Counter[str] = Counter()
    added: Counter[str] = Counter()
    lines: list[str] = []
    for cid in sorted(set(a) | set(b)):
        (sa, xa), (sb, xb) = a.get(cid, ("<none>", [])), b.get(cid, ("<none>", []))
        if (sa, xa) == (sb, xb):
            continue
        changed_rows += 1
        if sa != sb:
            flips[(sa, sb)] += 1
        ka = Counter((s["operation"], s["resource_id"]) for s in xa)
        kb = Counter((s["operation"], s["resource_id"]) for s in xb)
        removed.update(op for (op, _), n in (ka - kb).items() for _ in range(n))
        added.update(op for (op, _), n in (kb - ka).items() for _ in range(n))
        notes = []
        for system in args.system:
            ra = sorted({s["resource_id"] for s in xa if s["system_id"] == system})
            rb = sorted({s["resource_id"] for s in xb if s["system_id"] == system})
            if ra != rb:
                kind = "added" if not ra else "removed" if not rb else "changed"
                notes.append(f"{system} {kind}: {ra} -> {rb}")
        lines.append(f"{cid}: {sa} -> {sb}" + ("; " + "; ".join(notes) if notes else ""))
    print(f"rows changed: {changed_rows}")
    print("verdict flips: " + (", ".join(f"{x}->{y} {n}" for (x, y), n in sorted(flips.items())) or "none"))
    print(f"steps removed {sum(removed.values())}: " + ", ".join(f"{k} {v}" for k, v in removed.most_common()))
    print(f"steps added {sum(added.values())}: " + ", ".join(f"{k} {v}" for k, v in added.most_common()))
    for system in args.system:
        kinds = Counter(l.split(f"{system} ")[1].split(":")[0] for l in lines if f"{system} " in l)
        print(f"{system}: " + (", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) or "no change"))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
