"""Token ledger: per provider/model call counts and tokens, for usage_report.md."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ModelUsage:
    provider: str
    model: str
    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class TokenLedger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str], ModelUsage] = {}

    def record(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            u = self._rows.setdefault((provider, model), ModelUsage(provider, model))
            u.calls += 1
            u.input_tokens += input_tokens
            u.output_tokens += output_tokens

    def record_cache_hit(self, provider: str, model: str) -> None:
        with self._lock:
            self._rows.setdefault((provider, model), ModelUsage(provider, model)).cache_hits += 1

    def rows(self) -> list[ModelUsage]:
        with self._lock:
            return [ModelUsage(**asdict(u)) for _, u in sorted(self._rows.items())]

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([asdict(u) for u in self.rows()], indent=2), encoding="utf-8")
