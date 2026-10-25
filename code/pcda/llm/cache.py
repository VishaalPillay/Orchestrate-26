"""Content-hash disk cache for LLM responses.

The key covers the requested model, the prompt (system + user), the response
schema and the attachment bytes. It does not cover the provider that answered,
so a replay works whichever provider in the chain answered live. Files live
under code/.cache/llm/ (git-ignored, shipped in code.zip).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def content_key(model: str, prompt: str, schema: dict[str, Any] | None, attachments: list[bytes]) -> str:
    h = hashlib.sha256()
    h.update(json.dumps({"model": model, "prompt": prompt, "schema": schema}, sort_keys=True).encode())
    for blob in attachments:
        h.update(hashlib.sha256(blob).digest())
    return h.hexdigest()


class DiskCache:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        return self._root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        p = self._path(key)
        if not p.exists():
            return None
        with p.open(encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
        return data

    def put(self, key: str, value: dict[str, Any]) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, sort_keys=True, indent=1)
        os.replace(tmp, p)
