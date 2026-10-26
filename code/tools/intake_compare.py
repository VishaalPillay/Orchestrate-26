"""Compare an LLM intake model with the rule agent on all 550 requests.

    python code/tools/intake_compare.py --model gemini-3.5-flash-lite [--mode live|replay]

Runs LLMIntakeAgent (batches of 20, the intake.batch prompt) with the given
model, validates every answer (evidence_span verbatim in the text), and
compares it with RuleIntakeAgent. Prints agreement, every disagreement with
both rationales, discarded answers, calls and tokens, and writes
code/.cache/runs/intake_compare_<model>.json. CLASSIFY_REQUEST corroboration
needs per-case attribution, so it is not applied here; the pipeline's
decide_intake does that.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from pcda import paths  # noqa: E402
from pcda.agents.intake import LLMIntakeAgent, RuleIntakeAgent, decide_intake  # noqa: E402
from pcda.cases import load_requests  # noqa: E402
from pcda.llm.cache import DiskCache  # noqa: E402
from pcda.llm.client import RunMode, build_client  # noqa: E402
from pcda.llm.ledger import TokenLedger  # noqa: E402

COMPARE_ENV = "PCDA_INTAKE_COMPARE_MODEL"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--mode", choices=["live", "replay"], default="live")
    args = ap.parse_args()
    load_dotenv(paths.ENV_FILE)
    os.environ[COMPARE_ENV] = args.model  # the cache key is the model name, not this variable
    ledger = TokenLedger(paths.RUNS_DIR / "run_log.jsonl")
    client = build_client(RunMode(args.mode), DiskCache(paths.LLM_CACHE_DIR), ledger)
    cases = load_requests(paths.SAMPLE_REQUESTS) + load_requests(paths.DELETION_REQUESTS)
    agent = LLMIntakeAgent(client, model_env=COMPARE_ENV)
    agent.prepare([(c.case_id, c.request_text) for c in cases])
    rules = RuleIntakeAgent()

    agree = disagree = 0
    rows = []
    for c in cases:
        r = rules.classify(c.case_id, c.request_text)
        a = agent.answer(c.case_id)
        d = decide_intake(r, a, (), agent.rejected.get(c.case_id, ""))
        if a is None:
            status = "discarded"
        elif a.authorizes_deletion == r.authorizes_deletion:
            agree += 1
            status = "agree"
        else:
            disagree += 1
            status = "disagree"
        if status != "agree":
            rows.append({
                "case_id": c.case_id, "status": status, "text": c.request_text,
                "rules": {"authorizes_deletion": r.authorizes_deletion, "kind": r.request_kind, "rationale": r.rationale},
                "llm": None if a is None else {
                    "authorizes_deletion": a.authorizes_deletion, "kind": a.request_kind,
                    "span": a.evidence_span, "confidence": a.confidence, "rationale": a.rationale},
                "llm_rejected": agent.rejected.get(c.case_id, ""),
                "decision": {"authorizes_deletion": d.result.authorizes_deletion, "source": d.source},
            })
    kinds_agree = sum(
        1 for c in cases
        if (a := agent.answer(c.case_id)) is not None and a.request_kind == rules.classify(c.case_id, c.request_text).request_kind
    )
    usage = [u for u in ledger.rows() if u.model == args.model]
    summary = {
        "model": args.model, "requests": len(cases), "answered": len(agent.answers),
        "discarded": len(cases) - len(agent.answers), "agree": agree, "disagree": disagree,
        "request_kind_agree": kinds_agree,
        "calls": sum(u.calls for u in usage), "cache_hits": sum(u.cache_hits for u in usage),
        "errors": sum(u.errors for u in usage),
        "input_tokens": sum(u.input_tokens for u in usage), "output_tokens": sum(u.output_tokens for u in usage),
        "other_providers": [(u.provider, u.model, u.calls) for u in ledger.rows() if u.model != args.model],
    }
    print(json.dumps(summary, indent=1))
    for row in rows:
        llm = row["llm"]
        print(f"\n{row['case_id']} [{row['status']}] decision={row['decision']}")
        print(f"   text : {row['text']}")
        print(f"   rules: {row['rules']['authorizes_deletion']} {row['rules']['kind']} ({row['rules']['rationale']})")
        print(f"   llm  : {llm and (llm['authorizes_deletion'], llm['kind'], llm['span'], llm['rationale'])} {row['llm_rejected']}")
    out = paths.RUNS_DIR / f"intake_compare_{args.model.replace('/', '_')}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
