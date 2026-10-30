"""Build code.zip from the repository root: README.md and code/, with the model cache and without secrets.

    python code/tools/package_zip.py [--out code.zip]

Included: README.md, code/ (sources, prompts, tests, evaluation reports) and code/.cache/llm/ (cached model
answers that replay needs). Excluded: dataset/, .env, .venv, build and tool caches, the run ledgers under
code/.cache/runs/, and any archive file.
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv", "node_modules", ".git"}
SKIP_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".pyc"}
SKIP_NAMES = {".env", ".DS_Store"}
SKIP_TREES = {Path("code/.cache/runs")}


def wanted(rel: Path) -> bool:
    if any(part in SKIP_DIRS for part in rel.parts) or rel.name in SKIP_NAMES or rel.suffix in SKIP_SUFFIXES:
        return False
    return not any(tree in rel.parents or rel == tree for tree in SKIP_TREES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "code.zip")
    args = ap.parse_args()
    files = [ROOT / "README.md"] + sorted(p for p in (ROOT / "code").rglob("*") if p.is_file())
    kept = [p for p in files if wanted(p.relative_to(ROOT))]
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in kept:
            z.write(p, p.relative_to(ROOT).as_posix())
    print(f"{len(kept)} files -> {args.out} ({args.out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
