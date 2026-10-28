"""Run perception on every PNG in the manifest and print what it saw and what grounding kept.

    python code/tools/perceive_images.py --mode live      # one provider call per uncached image
    python code/tools/perceive_images.py                  # deterministic: cache only, no calls

Prints, per image: the model's raw records, then the grounded facts (kind,
resource, identifiers, observed_at source) and every dropped record, then
image-vs-text identifier conflicts. Writes the raw transcriptions to
code/.cache/runs/perception_<mode>.json for review.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from pcda import paths
from pcda.agents.perception import LLMPerceptionAgent
from pcda.facts import FactKind
from pcda.ingest.base import IngestReport, read_manifest
from pcda.ingest.image_adapter import ImageAdapter
from pcda.ingest.registry import build_adapters, ingest_all
from pcda.lineage import LineageGraph, resource_prefix
from pcda.llm.cache import DiskCache
from pcda.llm.client import RunMode, build_client
from pcda.llm.ledger import TokenLedger


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=[m.value for m in RunMode], default=RunMode.DETERMINISTIC.value)
    args = p.parse_args()
    load_dotenv(paths.ENV_FILE)
    mode = RunMode(args.mode)
    ledger = TokenLedger(paths.RUNS_DIR / "run_log.jsonl")
    client = build_client(mode, DiskCache(paths.LLM_CACHE_DIR), ledger)
    lineage = LineageGraph.from_csv(paths.SYSTEM_ACTIVITY)
    prefixes = frozenset(resource_prefix(c.resource_id) for c in lineage.claims)
    report = IngestReport()
    adapters = build_adapters(LLMPerceptionAgent(client), mode, report, prefixes)
    store = ingest_all(read_manifest(), adapters, report)
    image = adapters[".png"]
    assert isinstance(image, ImageAdapter)

    dump = {}
    for row in [r for r in read_manifest() if r.file_path.endswith(".png")]:
        screen = image.screens.get(row.file_id)
        print(f"\n=== {row.file_id} {Path(row.file_path).name} captured {row.captured_at:%Y-%m-%d %H:%M}")
        if screen is None:
            print("    no transcription (skipped)")
            continue
        dump[row.file_id] = asdict(screen)
        print(f"    screen={screen.screen!r} via {screen.provider}/{screen.model} cached={screen.cached} "
              f"records={len(screen.records)}")
        for r in screen.records:
            idents = [*r.emails, *r.phones, *r.user_ids, *(h[:12] + '…' for h in r.hashes)]
            print(f"    raw  {r.resource_id} system={r.system} ids={idents} states={list(r.states)} "
                  f"ts={[t for t, _ in r.timestamps]} fields={r.fields}")
        for f in (f for f in store if f.evidence_id == row.file_id and f.kind is not FactKind.LINK):
            print(f"    fact {f.kind.value:<13} {f.resource_id:<18} state={f.attrs.get('state')} "
                  f"pii={f.attrs.get('pii_fields')} ids={sorted(f.identifiers)} "
                  f"observed={f.observed_at:%Y-%m-%d %H:%M} ({f.attrs.get('observed_from')})")
    print("\ngrounding drops / warnings:")
    for w in report.warnings:
        if any(w.startswith(r.file_id) for r in read_manifest() if r.file_path.endswith(".png")):
            print("   ", w)
    print("image vs text conflicts:", report.conflicts or "none")
    out = paths.RUNS_DIR / f"perception_{mode.value}.json"
    out.write_text(json.dumps(dump, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nusage: {[(u.provider, u.model, u.calls, u.cache_hits, u.errors, u.input_tokens, u.output_tokens) for u in ledger.rows()]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
