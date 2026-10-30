"""Write code/evaluation/usage_report.md from the token ledger.

    python code/tools/usage_report.py

Reads code/.cache/runs/run_log.jsonl (one event per provider call or cache hit;
no prompts, answers or keys) and the usage_all_live.json of the last full live
run. Prices are assumptions, set below and printed in the report: the repository
holds no verified price list and every call ran on a free tier, so the billed
cost is 0.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pcda import paths

REQUESTS_TOTAL = 550  # 32 samples + 518 evaluation requests: the pipeline processes both files
REQUESTS_TEST = 518
# Calls from this hour on belong to the final configuration's sample-skeptic cache (iterations 20 and 21).
FINAL_SKEPTIC_FROM = "2026-10-25T08"

# USD per 1M tokens (input, output). ASSUMED reference rates, not read from a provider:
#   gemini-3.5-flash-lite  0.30 / 2.50  third-party calculator listing dated 2026-07-24 (trevorfox.com)
#   gemini-3.6-flash       1.50 / 9.00  assumed (no published rate found); borrowed from the gemini-3.5-flash listing (morphllm.com, June 2026)
# openai/gpt-oss-120b (Groq) answered only discarded fallback runs and is left unpriced.
PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.6-flash": (1.50, 9.00),
}

Key = tuple[str, str, str]  # task, provider, model


def cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    if model not in PRICES:
        return None
    pin, pout = PRICES[model]
    return (tokens_in * pin + tokens_out * pout) / 1_000_000


def money(value: float | None, places: int = 4) -> str:
    return "unpriced" if value is None else f"${value:.{places}f}"


def table(rows: dict[Key, list[int]]) -> list[str]:
    lines = [
        "| Task | Provider | Model | Provider calls | Cache hits | Input tokens | Output tokens | Est. cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    total = [0, 0, 0, 0]
    priced = 0.0
    for (task, provider, model), (calls, hits, tin, tout) in sorted(rows.items()):
        c = cost(model, tin, tout)
        priced += c or 0.0
        total = [a + b for a, b in zip(total, (calls, hits, tin, tout), strict=True)]
        lines.append(f"| {task} | {provider} | {model} | {calls} | {hits} | {tin:,} | {tout:,} | {money(c)} |")
    lines.append(
        f"| **Total** | | | **{total[0]}** | **{total[1]}** | **{total[2]:,}** | **{total[3]:,}** | **{money(priced)}** (priced models) |"
    )
    return lines


def aggregate(events: list[dict], keep: Callable[[dict], bool]) -> dict[Key, list[int]]:
    rows: dict[Key, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for e in events:
        if e["event"] not in ("call", "cache_hit") or not keep(e):
            continue
        row = rows[e["task"], e["provider"], e["model"]]
        if e["event"] == "call":
            row[0] += 1
            row[2] += e.get("input_tokens") or 0
            row[3] += e.get("output_tokens") or 0
        else:
            row[1] += 1
    return rows


def final_config(e: dict) -> bool:
    """Calls whose answers the final output reads from the cache."""
    if e["task"] == "skeptic":
        return e["provider"] == "gemini" and e["ts"][:13] >= FINAL_SKEPTIC_FROM
    if e["task"] == "intake.batch":
        return e["provider"] == "gemini"
    if e["task"] == "perception.image":
        return e["model"] == "gemini-3.6-flash"
    return e["task"] == "category.fallback"


def main() -> int:
    with (paths.RUNS_DIR / "run_log.jsonl").open(encoding="utf-8") as fh:
        events = [json.loads(line) for line in fh]
    final = json.loads((paths.RUNS_DIR / "usage_all_live.json").read_text(encoding="utf-8"))
    out = [
        "# Usage report",
        "",
        "Every model call went to a free tier (Google Gemini; Groq only as a fallback that the final configuration does not use),",
        "so the **billed cost is $0.00**. The cost columns are *estimates at assumed list prices*:",
        "",
        *[f"- `{m}`: ${a:.2f} per 1M input tokens, ${b:.2f} per 1M output tokens" for m, (a, b) in PRICES.items()],
        "",
        "The rates are assumptions, not provider-verified: the `gemini-3.5-flash-lite` rate is a third-party listing, and the",
        "`gemini-3.6-flash` rate is assumed (no published rate found). Change `PRICES` in `code/tools/usage_report.py` and rerun it to reprice.",
        "",
        "## 1. The final full-dataset run",
        "",
        "`python code/main.py --mode live --split all` (`LLM_PROVIDERS=gemini`) wrote the committed `output.csv`. The intake,",
        "category, perception and skeptic answers were already in the disk cache, so the run made only the provider calls",
        "below: 3 skeptic calls that re-ran one target left unfinished by the previous run. `--mode replay` reproduces the",
        "same `output.csv` byte for byte with 0 provider calls.",
        "",
        "| Provider | Model | Provider calls | Cache hits | Input tokens | Output tokens | Est. cost |",
        "|---|---|---|---|---|---|---|",
    ]
    f_in = f_out = f_calls = f_hits = 0
    f_cost = 0.0
    for r in final:
        c = cost(r["model"], r["input_tokens"], r["output_tokens"])
        f_cost += c or 0.0
        f_in += r["input_tokens"]
        f_out += r["output_tokens"]
        f_calls += r["calls"]
        f_hits += r["cache_hits"]
        out.append(
            f"| {r['provider']} | {r['model']} | {r['calls']} | {r['cache_hits']} | {r['input_tokens']:,} | {r['output_tokens']:,} | {money(c)} |"
        )
    out.append(f"| **Overall** | | **{f_calls}** | **{f_hits}** | **{f_in:,}** | **{f_out:,}** | **{money(f_cost)}** |")
    total = f_in + f_out
    out += [
        "",
        f"Requests processed: {REQUESTS_TOTAL} ({REQUESTS_TEST} evaluation + 32 samples). Tokens in this run: {total:,} in total, "
        f"{total / REQUESTS_TOTAL:.1f} per request; estimated cost {money(f_cost)} in total, {money(f_cost / REQUESTS_TOTAL, 6)} per request.",
        "",
        "## 2. Everything the final output depends on",
        "",
        "The answers that `--mode replay` reads, by task, from the ledger: intake (28 batches of 20 requests, gemini-3.5-flash-lite),",
        "the category fallback (2 names), screenshot perception (11 images, gemini-3.6-flash) and the sample-skeptic",
        f"conversations (60 calls; ledger calls from {FINAL_SKEPTIC_FROM}:00Z on: the regenerations of iterations 20 and 21 and the final run).",
        "The evaluation requests need no skeptic call (budget 0, iteration 13).",
        "",
    ]
    rows = aggregate(events, final_config)
    out += table(rows)
    tin = sum(r[2] for r in rows.values())
    tout = sum(r[3] for r in rows.values())
    est = sum(cost(m, r[2], r[3]) or 0.0 for (_, _, m), r in rows.items())
    out += [
        "",
        f"Per request over {REQUESTS_TOTAL}: {(tin + tout) / REQUESTS_TOTAL:,.1f} tokens ({tin / REQUESTS_TOTAL:,.1f} in, "
        f"{tout / REQUESTS_TOTAL:,.1f} out), {money(est / REQUESTS_TOTAL, 6)}. Over the {REQUESTS_TEST} evaluation requests: "
        f"{(tin + tout) / REQUESTS_TEST:,.1f} tokens, {money(est / REQUESTS_TEST, 6)}. Cache-hit counts are cumulative over every run in the ledger.",
        "The category rows include the 2 calls of the LLM-only experiment of iteration 12a; the perception row includes one retry.",
        "",
        "## 3. All provider calls recorded during development",
        "",
        "Every call in `code/.cache/runs/run_log.jsonl` since the first run, including superseded prompts, failed and",
        "discarded attempts, the Groq fallback runs, the skeptic's test-split run (iteration 13) and the negative control.",
        "",
    ]
    out += table(aggregate(events, lambda e: True))
    errors = sum(1 for e in events if e["event"] == "error")
    out += ["", f"Recorded provider errors (quota, timeouts, malformed answers; no tokens billed): {errors}.", ""]
    path = Path(__file__).resolve().parents[1] / "evaluation" / "usage_report.md"
    path.write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
