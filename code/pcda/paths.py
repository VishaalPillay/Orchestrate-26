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

_DATASET_FILES = {
    "DELETION_REQUESTS": "deletion_requests.csv",
    "SAMPLE_REQUESTS": "sample_deletion_requests.csv",
    "SAMPLE_OUTPUT": "sample_output.csv",
    "USER_IDENTITIES": "user_identities.csv",
    "USER_AUDIT_LOG": "user_audit_log.csv",
    "SYSTEM_ACTIVITY": "system_activity.csv",
    "DELETION_RULES": "deletion_rules.csv",
    "EVIDENCE_MANIFEST": "evidence_files.csv",
}


def use_dataset(root: Path) -> None:
    """Point every dataset path at another directory (custom evaluation overlays).

    Callers read paths as `paths.X` at call time, so rebinding the module
    globals is enough. Output, cache and .env paths are not touched.
    """
    global DATASET, EVIDENCE_DIR
    DATASET = Path(root).resolve()
    EVIDENCE_DIR = DATASET / "evidence"
    for name, file in _DATASET_FILES.items():
        globals()[name] = DATASET / file

OUTPUT_CSV: Path = REPO_ROOT / "output.csv"
ENV_FILE: Path = REPO_ROOT / ".env"

# Reproducibility artifacts. Kept out of git, shipped inside code.zip.
CACHE_DIR: Path = CODE_DIR / ".cache"
LLM_CACHE_DIR: Path = CACHE_DIR / "llm"
RUNS_DIR: Path = CACHE_DIR / "runs"
