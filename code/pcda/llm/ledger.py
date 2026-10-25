"""Token ledger: per provider/model call counts and tokens, for usage_report.md.

Every provider call, cache hit and provider error is also appended as one
JSON line to the run log (code/.cache/runs/run_log.jsonl by default). Rows
carry task, provider, model and token counts only: never a prompt, an answer
or a key.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ModelUsage:
    provider: str
    model: str
    calls: int = 0
    cache_hits: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class TokenLedger:
    def __init__(self, run_log: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str], ModelUsage] = {}
        self._run_log = run_log

    def _append(self, row: dict[str, object]) -> None:
        if self._run_log is None:
            return
        row = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **row}
        self._run_log.parent.mkdir(parents=True, exist_ok=True)
        with self._run_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def record(self, task: str, provider: str, model: str, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            u = self._rows.setdefault((provider, model), ModelUsage(provider, model))
            u.calls += 1
            u.input_tokens += input_tokens
            u.output_tokens += output_tokens
            self._append({"event": "call", "task": task, "provider": provider, "model": model,
                          "input_tokens": input_tokens, "output_tokens": output_tokens})

    def record_cache_hit(self, task: str, provider: str, model: str) -> None:
        with self._lock:
            self._rows.setdefault((provider, model), ModelUsage(provider, model)).cache_hits += 1
            self._append({"event": "cache_hit", "task": task, "provider": provider, "model": model})

    def record_error(self, task: str, provider: str, model: str, attempt: int, message: str) -> None:
        with self._lock:
            self._rows.setdefault((provider, model), ModelUsage(provider, model)).errors += 1
            self._append({"event": "error", "task": task, "provider": provider, "model": model,
                          "attempt": attempt, "message": message[:300]})

    def rows(self) -> list[ModelUsage]:
        with self._lock:
            return [ModelUsage(**asdict(u)) for _, u in sorted(self._rows.items())]

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([asdict(u) for u in self.rows()], indent=2), encoding="utf-8")
