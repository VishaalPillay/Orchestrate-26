# Handoff: Ghost Data Still Exists (PCDA)

Written 2026-10-24T19:23Z at the end of the evening session. Deadline **2026-10-25T12:30:00Z** (about 17h left when written). Start here, then read `AGENTS.md` (logging rules), `code/ARCHITECTURE.md` and `code/evaluation/evaluation_report.md`.

## Current scores

Samples (32 cases, `python code/evaluation/score.py --diff`):

| Metric | Value |
|---|---|
| Status accuracy | 32/32 (1.000) |
| Step precision / recall / F1 | 1.000 / 0.927 / 0.962 (179 of 193 gold steps, 0 extra) |
| evidence_id accuracy on matched steps | 179/179 |
| Exact-sequence matches | 20/32 |

Evaluation set: `output.csv` at the repo root comes from `--mode live` and is reproduced byte for byte by `--mode replay`. It has 518 sorted rows and passes the validator: 390 INCOMPLETE, 117 COMPLETE, 11 NOT_APPLICABLE. Of those 11, 10 come from intake and 1 (case_61) is the no-provable-obligation fallback. `--mode deterministic` differs in exactly 1 row (case_45, see below).

Custom overlays: 7/7 pass. Step-aligned field accuracy is 1.000 on every field, so every sample loss is a missing step, never a wrong one.

Reproduce (no provider calls; everything comes from `code/.cache/llm/`):

```bash
.venv/Scripts/python.exe code/main.py --mode replay --split all          # byte-identical to the committed output.csv
.venv/Scripts/python.exe code/main.py --mode deterministic --split all   # rules only; differs in case_45
.venv/Scripts/python.exe code/evaluation/score.py --diff
.venv/Scripts/python.exe -m pytest code/tests -q
.venv/Scripts/python.exe code/evaluation/custom_cases.py      # 7 overlays, all pass
.venv/Scripts/python.exe code/evaluation/main.py --write      # rewrites the Metrics and Custom sections
```

`main.py --dataset-dir DIR --case ID --out FILE` runs single cases on a copied dataset and never writes `output.csv`.

## What is committed (main)

| Commit | What |
|---|---|
| `1b3c5cb` | Deterministic core: rule intake, static category map, compiled policy, solver, planner, scorer, first output |
| `a45742f` | Joint activity attribution for accounts (`activity_joint`) |
| `bb8ee43` | support_replica contact-index events → SECONDARY_IDENTIFIER |
| `41c1579`, `d8ca803` | Analytics joint pass, restricted to fallback cases |
| `94e072b` | Evaluation report with the iteration log |
| `1709f3b` | One OpenAI-compatible LLM client (Gemini + Groq), cache, ledger, perception agent, image grounding |
| `d00e1fc` | Perception on gemini-3.6-flash with resource-id prompt rules |
| `a201fb1` | Burden of proof: an existing flow or archive with no evidenced guard state is OPEN |
| `2cfdc44` | ARCHITECTURE.md, the iteration log and handoff for the evening session |
| `6786f09`, `212eacc` | Burden-of-proof flip audit (case_55: correct); custom overlay cases and the full metrics report |
| (this commit) | LLM intake + hybrid decision, LLM category fallback, aligned field accuracy, model and strategy comparison, live output |

The working tree is clean after this commit. 73 tests pass. `log.txt` (git-ignored) has every turn.

**Not in git, but required:** `code/.cache/llm/` holds the 11 screenshot transcriptions. It must be shipped inside `code.zip`. Without it, deterministic runs skip every PNG and the scores above won't reproduce. `.env` is git-ignored and must never be zipped. It currently sets `PERCEPTION_MODEL=gemini-3.6-flash`, because the 3.8 and 3.7 quotas were exhausted.

## Open issues

1. ~~Live and replay modes stop at intake~~ **resolved**: the LLM intake runs, and replay reproduces live byte for byte. The skeptic is still the no-op in every mode.
2. **Screenshot-only audit records have no category in deterministic mode only.** In live/replay the LLM category fallback maps `screen:Account events` to SECURITY_AUDIT_LOG (case_45). Deterministic still flags them: `audit_record_001`, `002`, `004`, `005` and `006` (evidence_37) are flagged `category_unknown`, because their only fact has table `screen:Account events` and they have no activity row. `audit_record_003` resolves through an activity row. Fix idea: let a perception fact fall back to the category of its canonical system (`security_service` → SECURITY_AUDIT_LOG via `_ACTIVITY_TARGETS`), or add the screen title to the inferred list. Affects the cases whose subject is `user_03`.
3. **116 `unknown_state` audit records.** Their only evidence was captured after `audit_at`. In INCOMPLETE cases they are still verified with `""`, which matches gold, but in COMPLETE cases they are dropped from the steps. That's probably right, and unverified on the evaluation set.
4. **case_61 is the only fallback.** `object_19` appears only in activity-catalog rows: no fact, and an unknown category (`object_storage`). Nothing on any screen decides it.
5. **Activity-joint outliers.** case_45 (row 35 hours before `reported_at`) and case_493 (−131 min) are accepted picks far outside the sample timing; both are flagged `skeptic_pending`. The skeptic is a no-op in deterministic mode.
6. **LLM quota** (gemini-3.6-flash also ran out during the intake comparison, so its column is pending). The free tier exhausted gemini-3.8-flash and gemini-3.7-flash today. gemini-3.6-flash, 3.5-flash, 3.5-flash-lite and 3.1-flash-lite answered a probe. Groq's `gpt-oss-120b` rejects images; Groq's `qwen/qwen3.8-27b` accepts images ≥ 32 px and could be the vision fallback. Calls can take 2 minutes; use `LLM_TIMEOUT_S=300`.
7. **`check_attribution.py` doesn't run the joint passes,** so its 90/111 understates current attribution.

## Known accepted misses (samples, 14 steps)

- 6 VERIFY_RECORD_ANONYMIZED on identifier-less analytics rows (cases 03, 21, 25–28). After anonymization they carry no identifier. The analytics joint pass is restricted to fallback cases on purpose (iteration 8), and 3 of these rows are SUCCESS on NOT_STARTED cases, which the result-matching rule excludes.
- 5 VERIFY_RECORD_ABSENT accounts still contested in their deletion window (cases 18, 23, 28, 29, 32). Nothing in the data separates the candidates.
- 2 VERIFY_RECORD_PRESENT for `audit_record_6645` and `audit_record_5569`, and 1 VERIFY_RECORD_ABSENT for `account_004`. None of them appears in any participant file or screen.

Rejected experiments, don't retry without new evidence: deletion-run reconstruction (precision 0.80), unrestricted analytics joint pass (3 near-false-positives on samples), and a newer snapshot overriding an older record fact (breaks samples 05 and 06).

## TODO for tomorrow (in order)

1. ~~LLM intake~~, ~~screenshot-only categories (live)~~ and ~~the LLM category fallback test~~ are **done** (iterations 11–12; the fallback recovers neither inferred name, so the static map stays primary).
2. Re-run `code/tools/intake_compare.py --model gemini-3.6-flash` once its quota resets, and fill the comparison table.
3. Optional: a deterministic category for `screen:Account events`, so deterministic matches live on case_45.
4. **Skeptic** (`SKEPTIC_MODEL`) on `activity_temporal` and `activity_joint` picks, starting with case_45 and case_493. It stays advisory only.
5. ~~Custom evaluation cases~~ **done**: 7 dataset overlays in `code/evaluation/custom_cases.py` (all pass), and metrics via `code/evaluation/main.py --write`. Candidates to add: the case_33 replica-vs-snapshot ordering and a burden-of-proof overlay.
6. **`evaluation/usage_report.md`** from the final full run's ledger (`code/.cache/runs/run_log.jsonl` and `usage_*.json`): providers, models, calls, tokens, cost per request.
7. **README.md** setup and run instructions, and **packaging** of `code.zip`: code, prompts, config, README and `evaluation/`, plus `code/.cache/llm/`; never `dataset/`, `.venv`, `.env` or nested archives. Submit `output.csv`, `code.zip` and `log.txt` at https://wwww.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists
8. Leave 2 hours before the deadline for a clean-clone run that reproduces `output.csv` byte for byte from the shipped cache.
