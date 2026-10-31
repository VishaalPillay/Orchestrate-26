# Ghost Data Still Exists: PCDA deletion-audit agent

HackerRank Orchestrate (October 2026). For every request in `dataset/deletion_requests.csv` the agent decides whether deletion was actually requested, reconstructs the person's current and historical identifiers, finds every remaining copy and recreation path at `audit_at`, applies `deletion_rules.csv`, and writes `output.csv` (`case_id,deletion_status,steps_json`).

**Principle: the model reads, the code decides.** LLMs do intake, screenshot transcription, a category fallback and an advisory skeptic. They never write a status, a step or a citation: code grounds every observation against the evidence and solves policy obligations deterministically. Design, modules and failure handling are in [`code/ARCHITECTURE.md`](code/ARCHITECTURE.md); the evaluation (accuracy, custom cases, iteration log, honest limits) is in [`code/evaluation/evaluation_report.md`](code/evaluation/evaluation_report.md); model usage in [`code/evaluation/usage_report.md`](code/evaluation/usage_report.md).

## Results

On the 32 public samples (`python code/evaluation/score.py --diff`; gold is read only by the scorer):

| Metric | Value |
|---|---|
| Deletion-status accuracy | 32/32 (1.000) |
| Step precision / recall / F1 | 1.000 / 0.984 / **0.992** (190 of 193 gold steps, 0 extra) |
| Evidence-id accuracy on matched steps | 190/190 |
| Step-aligned field accuracy | 1.000 on every field |
| Exact ordered sequences | 29/32 |
| Custom evaluation overlays | 8/8 |

The 3 missed steps (`audit_record_6645`, `audit_record_5569`, `account_004`) appear in no participant file. The evaluation set: 518 rows (355 INCOMPLETE, 153 COMPLETE, 10 NOT_APPLICABLE), validated before writing. Limits are listed honestly in the evaluation report.

## Setup

Python 3.13 (the version used; no 3.13-only features are intended).

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r code/requirements.txt    # Windows
# .venv/bin/python -m pip install -r code/requirements.txt          # macOS / Linux
```

Runtime dependencies are only `python-dotenv`, `openai` and `pymupdf` (pinned in `code/requirements.txt`); tests and linters are in `code/requirements-dev.txt`. `dataset/` must sit at the repository root. Model providers are optional (see "Run modes"); replay needs neither a key nor a `.env`, because the model names that produced the cache are defaults in `code/pcda/config.py` (`MODEL_DEFAULTS`). Their settings come only from the environment or an uncommitted `.env` (names only, never values, are in the code): `LLM_PROVIDERS`, `<NAME>_BASE_URL`, `<NAME>_API_KEY`, `INTAKE_MODEL`, `CATEGORY_MODEL`, `PERCEPTION_MODEL`, `SKEPTIC_MODEL`, `GROQ_FALLBACK_MODEL`, `LLM_RPM_LIMIT`, `LLM_DISABLE_THINKING`, `LLM_TIMEOUT_S`.

## Run

```bash
python code/main.py --mode replay --split all
```

This writes `output.csv` at the repository root (and the sample predictions to `code/.cache/runs/sample_output.csv`) with **no provider call**, from the cached model answers in `code/.cache/llm/` (shipped inside `code.zip`; without it the 11 screenshots cannot be read and results differ). It is byte-identical to the submitted `output.csv`.

| Mode | Provider calls | Use |
|---|---|---|
| `replay` | never; a cache miss is an error | reproduce the submitted output |
| `deterministic` | never; rules only, cached screenshots | model-free baseline (differs from replay in 1 row, case_45; unresolved categories are only flagged here) |
| `live` | per `LLM_PROVIDERS`, cache first | refresh the cache after a prompt or data change |

Useful commands:

```bash
python code/evaluation/score.py --diff          # samples: status, step P/R/F1, field accuracy, per-case diff
python code/evaluation/custom_cases.py          # 8 overlay cases
python code/evaluation/main.py --write          # rewrite the Metrics and Custom sections of the report
python code/tools/usage_report.py               # rewrite code/evaluation/usage_report.md (needs the local ledgers in code/.cache/runs/, which are not shipped in code.zip)
python code/tools/clock_validation.py           # label-free validation of the run clock and matching rule
python code/tools/joint_order_check.py          # order independence of the activity linking
python -m pytest code/tests -q                  # tests
```

`main.py --dataset-dir DIR --case ID --out FILE` runs single cases on a copied dataset and never writes `output.csv`.

## Pipeline

intake (rules plus LLM, verbatim-span checked) -> identity closure (current and historical identifiers, hashes) -> resource attribution by identifier tiers -> activity linking for what identifiers cannot reach (the deletion worker's run clock, ownership exclusivity and a matching rule over all 550 requests) -> bitemporal fact store `as_of(audit_at)` -> category resolver -> policy obligations -> solver -> planner -> validator. Evidence created after `audit_at` is never used for state or citations.

## Repository layout

```text
code/main.py            CLI
code/pcda/              pipeline, identity, runclock, solver, planner, validator, ingest/, agents/, llm/
code/evaluation/        score.py, custom_cases.py, main.py, evaluation_report.md, usage_report.md
code/tools/             validation, audit and spot-check tools
code/tests/             pytest suite
code/.cache/llm/        cached model answers (needed for replay; ship it)
```

Secrets are read from the environment only. `.env`, `.venv`, `dataset/` and `__pycache__` are not part of `code.zip`.
