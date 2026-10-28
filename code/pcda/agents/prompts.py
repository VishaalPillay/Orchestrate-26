"""Load prompts from prompts.md by task name."""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

PROMPTS_FILE = Path(__file__).with_name("prompts.md")


@cache
def load(task: str) -> tuple[str, str]:
    """(system, user) for one `## task` section."""
    text = PROMPTS_FILE.read_text(encoding="utf-8")
    sections = re.split(r"^## ", text, flags=re.MULTILINE)
    for section in sections[1:]:
        name, _, body = section.partition("\n")
        if name.strip() != task:
            continue
        system = re.search(r"^### system\n(.*?)(?=^### |\Z)", body, re.MULTILINE | re.DOTALL)
        user = re.search(r"^### user\n(.*?)(?=^### |\Z)", body, re.MULTILINE | re.DOTALL)
        if not system or not user:
            raise ValueError(f"prompt {task!r} needs ### system and ### user")
        return system.group(1).strip(), user.group(1).strip()
    raise KeyError(f"no prompt section {task!r} in {PROMPTS_FILE}")
