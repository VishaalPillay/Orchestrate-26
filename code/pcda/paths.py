"""Repository-relative paths. Nothing here depends on the working directory."""

from __future__ import annotations

from pathlib import Path

CODE_DIR: Path = Path(__file__).resolve().parent.parent
REPO_ROOT: Path = CODE_DIR.parent
DATASET: Path = REPO_ROOT / "dataset"
EVIDENCE_DIR: Path = DATASET / "evidence"

DELETION_REQUESTS: Path = DATASET / "deletion_requests.csv"
SAMPLE_REQUESTS: Path = DATASET / "sample_deletion_requests.csv"
SAMPLE_OUTPUT: Path = DATASET / "sample_output.csv"
USER_IDENTITIES: Path = DATASET / "user_identities.csv"
USER_AUDIT_LOG: Path = DATASET / "user_audit_log.csv"
SYSTEM_ACTIVITY: Path = DATASET / "system_activity.csv"
DELETION_RULES: Path = DATASET / "deletion_rules.csv"
EVIDENCE_MANIFEST: Path = DATASET / "evidence_files.csv"

OUTPUT_CSV: Path = REPO_ROOT / "output.csv"
ENV_FILE: Path = REPO_ROOT / ".env"

# Reproducibility artifacts. Kept out of git, shipped inside code.zip.
CACHE_DIR: Path = CODE_DIR / ".cache"
LLM_CACHE_DIR: Path = CACHE_DIR / "llm"
RUNS_DIR: Path = CACHE_DIR / "runs"
