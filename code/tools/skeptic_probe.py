"""Probe native tool calling on each provider before the skeptic is built on it.

    python code/tools/skeptic_probe.py [--gemini-model gemini-3.5-flash] [--provider gemini|groq ...]

One two-turn round trip per provider: the model must call `lookup`, receive a
result, and answer from it. A single request would not exercise the echo of
the assistant tool call (Gemini 3 thought signatures live there). Each
provider is measured alone, with a throwaway cache. Prints what was sent and
received in shape only: no prompts beyond the fixed probe, no keys.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from pcda import paths
from pcda.llm.cache import DiskCache
from pcda.llm.client import LLMClient, OpenAICompatProvider, RunMode
from pcda.llm.ledger import TokenLedger

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Look up a record by key. Returns compact JSON.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        },
    }
]
SYSTEM = "You are a careful assistant. Use the lookup tool when asked about a key. Never guess a value."
USER = 'What is the value for key "alpha"? Call lookup, then reply with only {"value": <number>}.'


def probe(name: str, model_env: str) -> dict[str, object]:
    base_url = os.environ.get(f"{name.upper()}_BASE_URL", "").strip()
    api_key = os.environ.get(f"{name.upper()}_API_KEY", "").strip()
    if not (base_url and api_key):
        return {"provider": name, "ok": False, "error": "not configured"}
    rpm = int(os.environ.get("LLM_RPM_LIMIT", "10") or "10")
    no_thinking = os.environ.get("LLM_DISABLE_THINKING", "0").strip() == "1"
    provider = OpenAICompatProvider(name, base_url, api_key, rpm, no_thinking)
    ledger = TokenLedger(None)
    with tempfile.TemporaryDirectory() as tmp:
        client = LLMClient(RunMode.LIVE, DiskCache(Path(tmp)), ledger, [provider])
        messages: list[dict[str, object]] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER},
        ]
        report: dict[str, object] = {"provider": name, "ok": False}
        try:
            t1 = client.complete_turn("probe.tools", model_env, messages, TOOLS)
            report["model"] = t1.model
            calls = t1.message.get("tool_calls") or []
            report["turn1_tool_calls"] = [c["function"]["name"] for c in calls]
            report["turn1_args"] = [c["function"]["arguments"] for c in calls]
            report["turn1_extra_content"] = [sorted((c.get("extra_content") or {}).keys()) for c in calls]
            report["turn1_tokens"] = [t1.input_tokens, t1.output_tokens]
            if not calls:
                report["error"] = "turn 1 made no tool call"
                report["turn1_text"] = str(t1.message.get("content"))[:200]
                return report
            messages.append(t1.message)
            for c in calls:
                messages.append(
                    {"role": "tool", "tool_call_id": c["id"], "content": json.dumps({"key": "alpha", "value": 42})}
                )
            t2 = client.complete_turn("probe.tools", model_env, messages, TOOLS)
            report["turn2_text"] = str(t2.message.get("content"))[:200]
            report["turn2_more_calls"] = len(t2.message.get("tool_calls") or [])
            report["turn2_tokens"] = [t2.input_tokens, t2.output_tokens]
            report["ok"] = "42" in str(t2.message.get("content"))
            # The cache must replay the whole conversation without a provider.
            replay = LLMClient(RunMode.REPLAY, client.cache, TokenLedger(None), [])
            r1 = replay.complete_turn("probe.tools", model_env, messages[:2], TOOLS)
            report["replay_turn1_cached"] = r1.cached and r1.message == t1.message
        except Exception as exc:  # the probe reports every failure shape
            report["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
        return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gemini-model", default="gemini-3.5-flash")
    ap.add_argument("--groq-model", default=None)
    ap.add_argument("--provider", action="append", choices=["gemini", "groq"], default=[])
    args = ap.parse_args()
    load_dotenv(paths.ENV_FILE)
    os.environ["SKEPTIC_MODEL"] = args.gemini_model
    if args.groq_model:
        os.environ["GROQ_FALLBACK_MODEL"] = args.groq_model
    for name in args.provider or ["gemini", "groq"]:
        print(json.dumps(probe(name, "SKEPTIC_MODEL"), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
