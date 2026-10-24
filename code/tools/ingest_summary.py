"""Ingest every manifest file and print fact counts.

    python code/tools/ingest_summary.py

Prints a table of fact counts per evidence_id and per FactKind, the totals,
and the manifest files that produced zero facts (with the reason when the
adapter recorded one). Deterministic mode only: nothing here calls a provider.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pcda.agents.perception import RawObservation  # noqa: E402
from pcda.facts import FactKind  # noqa: E402
from pcda.ingest.base import IngestReport, ManifestRow, read_manifest  # noqa: E402
from pcda.ingest.registry import build_adapters, ingest_all  # noqa: E402
from pcda.llm.client import RunMode  # noqa: E402


class _NoPerception:
    """Deterministic mode never calls perception."""

    def extract_image(self, path: Path, row: ManifestRow) -> list[RawObservation]:
        raise RuntimeError("perception is not available in deterministic mode")

    def extract_text(self, text: str, row: ManifestRow) -> list[RawObservation]:
        raise RuntimeError("perception is not available in deterministic mode")


def main() -> int:
    report = IngestReport()
    manifest = read_manifest()
    store = ingest_all(manifest, build_adapters(_NoPerception(), RunMode.DETERMINISTIC, report), report)

    per_file: dict[str, Counter[FactKind]] = defaultdict(Counter)
    for fact in store:
        per_file[fact.evidence_id][fact.kind] += 1

    kinds = list(FactKind)
    short = {k: k.value[:9] for k in kinds}
    header = f"{'evidence_id':<12}{'file':<58}" + "".join(f"{short[k]:>10}" for k in kinds) + f"{'total':>8}"
    print(header)
    print("-" * len(header))
    totals: Counter[FactKind] = Counter()
    for row in manifest:
        counts = per_file.get(row.file_id, Counter())
        totals.update(counts)
        name = Path(row.file_path).name
        name = name if len(name) <= 56 else name[:53] + "..."
        print(
            f"{row.file_id:<12}{name:<58}"
            + "".join(f"{counts.get(k, 0):>10}" for k in kinds)
            + f"{sum(counts.values()):>8}"
        )
    print("-" * len(header))
    print(f"{'TOTAL':<12}{'':<58}" + "".join(f"{totals.get(k, 0):>10}" for k in kinds) + f"{len(store):>8}")

    print("\nFacts per FactKind")
    for k in kinds:
        print(f"  {k.value:<14}{totals.get(k, 0):>7}")

    reasons = {s.evidence_id: s.reason for s in report.skipped}
    zero = [r for r in manifest if not per_file.get(r.file_id)]
    print(f"\nFiles with zero facts: {len(zero)} of {len(manifest)}")
    for r in zero:
        print(f"  {r.file_id}  {r.file_path}  -- {reasons.get(r.file_id, 'NO REASON RECORDED')}")

    other = [w for w in report.warnings if "image skipped" not in w]
    print(f"\nAdapter warnings (excluding skipped images): {len(other)}")
    for w in other:
        print(f"  {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
