# Handoff: Ghost Data Still Exists (PCDA)

Rewritten 2026-10-25 after iterations 20 and 21 (merged to `main`). Deadline **2026-10-25T12:30:00Z**. Start here, then read `AGENTS.md` (logging rules), `code/ARCHITECTURE.md` and `code/evaluation/evaluation_report.md`.

## Current scores

Samples (32 cases, `python code/evaluation/score.py --diff`):

| Metric | Value |
|---|---|
| Status accuracy | 32/32 (1.000) |
| Step precision / recall / F1 | 1.000 / 0.984 / 0.992 (190 of 193 gold steps, 0 extra) |
| evidence_id accuracy on matched steps | 190/190 |
| Exact-sequence matches | 29/32 |
| Custom overlays | 8/8 |

Evaluation set: `output.csv` (518 sorted rows, 355 INCOMPLETE / 153 COMPLETE / 10 NOT_APPLICABLE; the 10 NOT_APPLICABLE all come from intake; case_61 is the one INCOMPLETE with `steps_json=[]`, the flagged fallback of iteration 22) is written by `--mode replay` from the cache of the last live run and is byte-identical to it. `--mode deterministic` differs in exactly 1 row (case_45).

Reproduce (no provider calls):

```bash
.venv/Scripts/python.exe code/main.py --mode replay --split all
.venv/Scripts/python.exe code/evaluation/score.py --diff
.venv/Scripts/python.exe -m pytest code/tests -q
.venv/Scripts/python.exe code/evaluation/custom_cases.py
```

## What changed since the last handoff

- **Iteration 20** (`aa536f1`): a learned run clock (`code/pcda/runclock.py`) filters account candidates by band and phase, and `identity.forced_by_matching` assigns only edges present in every maximum matching. Samples F1 0.962 -> 0.976; test +94 account steps, 2 removed, 0 verdict changes; test deletion cases without an account step 144 -> 52.
- **Iteration 21** (`27692cf`): the same clock links identifier-less analytics rows through the case's account row (per-template windows and residues). Samples F1 0.976 -> 0.992; test +53 analytics steps, 0 verdict changes. The old unanchored analytics pass stays as the no-provable-obligation fallback.
- Everything learned is printed by `main.py` and comes from the 238 evaluation requests whose account is linked by identifiers; `sample_output.csv` is read only by `score.py` (`test_runclock.py::test_pipeline_never_opens_sample_output_csv`).
- `PCDA_NO_RUN_CLOCK=1` switches the clock off for ablations.
- New tools: `clock_validation.py`, `clock_spotcheck.py`, `usage_report.py`, `package_zip.py`.
- Shipping check found that a checkout without `.env` missed the perception cache (the cache key includes the model name, which only `.env` set), turning 5 cases NOT_APPLICABLE. The model names are now defaults in `config.MODEL_DEFAULTS`; a clean unzip plus `dataset/` replays byte-identically without `.env`.

## Known misses and limits (details in the evaluation report)

- 3 sample steps (`audit_record_6645`, `audit_record_5569`, `account_004`) appear in no participant file.
- 52 of 507 test deletion cases have no account step (31 INCOMPLETE, 21 COMPLETE; invariant 3: 98 -> 31 on test): 8 have no status-matched row in the band, 6 have one that phase, ownership or another request removes, 38 have two or more the clock and matching cannot separate. Tie-breaking by time was measured and rejected (iteration 18).
- Analytics picks rest on timing: 64 correct, 0 wrong when hidden and re-linked, 0 extra steps on the 19 sample cases with no analytics; test cases with no analytics record cannot be checked.
- The sample skeptic cache was regenerated for the final flags (gemini-3.5-flash-lite only). Any change to flag or reason strings needs a replay run and may need `--mode live --split sample` with `LLM_PROVIDERS=gemini`.

## Packaging notes

- `code/.cache/llm/` must ship inside `code.zip`; `.env`, `.venv`, `dataset/`, `__pycache__` and `code/.cache/runs/` must not (the run ledgers and `log` outputs are large and unneeded for replay).
- Submit `output.csv`, `code.zip` and `log.txt` at https://wwww.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists
- Leave time for a clean-clone check: unzip, copy `dataset/` in, run `--mode replay --split all`, compare `output.csv` byte for byte.
