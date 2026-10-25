# Evaluation report

All numbers are on the 32 public sample cases (`dataset/sample_deletion_requests.csv` against `dataset/sample_output.csv`), produced by:

```text
python code/main.py --mode deterministic --split all
python code/evaluation/score.py --diff
```

Step metrics match steps as a multiset on `(phase, system_id, resource_id, operation, state)`. Exact matches mean the whole ordered `steps_json`, evidence included, equals gold. Every run is deterministic and makes no LLM call.

## Iteration log

Iterations 1–3 came before the solver existed, so they have no step-level or status metrics. Their column shows resource attribution against the resources in gold (`code/tools/check_attribution.py`).

| # | Iteration | Change | Step P / R / F1 | Status accuracy | Exact matches | Kept / reverted |
|---|---|---|---|---|---|---|
| 1 | Attribution baseline | Identity closure (current + historical identifiers, hashes) and per-case resource attribution by tier, with a staleness gate | n/a (no solver). Attribution recall 82/111 = 0.739, precision 1.000 | n/a | n/a | Kept |
| 2 | Ownership/state split | Ownership may use evidence of any capture time, with a post-audit guardrail; state and citations stay `as_of(audit_at)` | n/a. Attribution recall 90/111 = 0.811, precision 1.000 | n/a | n/a | Kept |
| 3 | Deletion-run signal | Group deletion_worker rows into runs and attribute a run's account/analytics targets | n/a. Best attribution precision 0.80 (below the 0.98 bar) | n/a | n/a | Reverted |
| 4 | Deterministic solver | Rule intake, static category map, compiled policy, obligation solver, planner with citation rule, validator | 1.000 / 0.876 / 0.934 | 30/32 (0.938) | 16/32 | Kept |
| 5 | Joint accounts | `activity_joint` tier for accounts linked only by a deletion_worker DELETE_RECORD row: result matches `reported_status`, other users' accounts eliminated, one row per request across all 550 requests, contested accounts refused | 1.000 / 0.907 / 0.951 | 30/32 (0.938) | 19/32 | Kept |
| 6 | support_replica categories | `contact_query_completed` and `deletion_marker_check` (legacy contact-index entry, evidence_61) mapped to SECONDARY_IDENTIFIER, so the record is deleted | 1.000 / 0.922 / 0.960 | 31/32 (0.969) | 20/32 | Kept |
| 7 | Joint analytics | Same joint rule for analytics_platform ANONYMIZE_RECORD rows, only for cases with no analytics link | 1.000 / 0.927 / 0.962 | 32/32 (1.000) | 20/32 | Superseded by 8 |
| 8 | Joint analytics, fallback only | The analytics joint pass applies only when a case would otherwise have no provable obligation (the NOT_APPLICABLE fallback) | 1.000 / 0.927 / 0.962 | 32/32 (1.000) | 20/32 | Kept |
| 9 | Screenshot perception (first-pass prompt) | One gemini-3.8-flash call per PNG (11), cached; records grounded in code (known id prefix, normalized identifiers, timestamps clamped to captured_at, fixed state words, `src=perception` below db_row) | 1.000 / 0.927 / 0.962 | 32/32 (1.000) | 20/32 | Kept |
| 9b | Perception model and prompt fix | `PERCEPTION_MODEL` moved from gemini-3.8-flash (quota exhausted) to gemini-3.6-flash, since gemini-3.7-flash was also over quota and a one-call probe showed 3.6 has quota and accepts images. Resource-id rules added to the prompt; a misplaced but unique visible id is adopted in grounding | 1.000 / 0.927 / 0.962 | 32/32 (1.000) | 20/32 | Kept |
| 9a | Newer snapshot overrides older record fact | Experiment: a strictly newer non-vacuous covering snapshot marks a DELETE-rule record absent | 1.000 / 0.896 / 0.945 | 30/32 (0.938) | n/a | Reverted |

### Notes per iteration

- **5, joint accounts.** On samples the rule accepted 11 accounts and all 11 were correct; 6 of them were new beyond `activity_temporal`. On the evaluation set it assigns 120 accounts and refuses 4 contested cases. Two evaluation picks sit far outside the sample timing (case_45 at −2086 min, case_493 at −131 min from `reported_at`); every pick is flagged for the skeptic.
- **6, categories.** These names, and case_12's `contact_lookup_completed` + `email_opt_in`, are listed in `category.INFERRED_FROM_EVIDENCE` with their justification. A later run will remove them to test whether the LLM category fallback recovers them.
- **7, joint analytics: caution.** On samples the rule accepted 1 row (case_04 `analytics_901`) and it was correct, so precision is 1.000 and the rule is kept. Its recall is limited: 3 of the 7 gold analytics rows are SUCCESS rows on NOT_STARTED cases (25, 26, 28), which the result-matching rule excludes by design. The weakness is that "no analytics link" does not mean the subject has an analytics record. In 3 sample cases with no analytics in gold (01, 14, 31), the rule ended with a single candidate row and was stopped only because another case contested it. On the evaluation set it assigns 40 rows, and their precision is not supported by the sample evidence the way the account rule's is.
- **8, fallback only: decision.** One correct pick against three near-false-positives does not justify 40 evaluation assignments. The pass is kept only where the alternative is a case with no provable obligation, which can only be written as the NOT_APPLICABLE fallback. There a weak link costs little and a correct one turns the case into a scorable row. On samples this is exactly case_04, so the scores do not change. On the evaluation set it affects 0 cases: none of the 6 fallback cases (39, 50, 53, 55, 61, 63) has a single analytics candidate. Compared with iteration 7, the steps of 14 evaluation rows revert and no status changes.

- **9, perception.** No sample gold step cites a screenshot, so sample scores are unchanged; the check that matters is the manual review. I compared every PNG with its transcription record by record. There were **no hallucinated records**: no invented `tombstone_17` on the reference-registry screen, no `object_19` among the storage file names, and no restore state invented for backups that show none. There were **2 missed records**. On evidence_19 the model used `user_20` as the id of `analytics_7438`, and on evidence_28 it used ticket number `CS-5034` and filed `support_1034` as a field. Grounding dropped both records, so nothing wrong entered the store. Grounded records per image: 04: 1, 09: 2, 14: 2, 19: 0, 23: 4, 28: 0, 32: 4, 37: 6, 42: 0, 46: 0, 60: 1. None of `audit_record_6645`, `audit_record_5569`, `account_004` or `tombstone_440` appears on any screen. On the evaluation set the NOT_APPLICABLE fallbacks drop from 6 to 4 (cases 39 and 63 now get INCOMPLETE). 7 rows change in total. A prompt fix for the two misses (resource_id format rules; user_N, ticket, order and file names are never ids) recovered `support_9538` on the one image it reached, but Gemini's quota ran out before the other 10 were re-transcribed. The fix is saved at `code/.cache/pending/prompts_v2.md`, and the committed prompt is the first-pass one so that runs reproduce from the cache.
- **9b, model switch.** Compared again record by record against the screenshots: all 3 first-pass misses are recovered (`analytics_7438`, `support_1034`, `support_9538`) and there are no hallucinated records. On evidence_60, gemini-3.6-flash put `marketing_001` among the identifiers instead of in resource_id; grounding adopts it because it is the only id-shaped value transcribed for that record (two candidates would be dropped, never guessed). Records per image: 04: 2, 09: 2, 14: 2, 19: 1, 23: 4, 28: 1, 32: 4, 37: 6, 42: 0, 46: 0, 60: 1. Evaluation-set fallbacks fall from 4 to 2 (cases 50 and 53 become INCOMPLETE). The remaining two are genuine unknowns: case_55's backup shows no restore setting and case_61's `object_19` is on no screen. Usage for the run that produced this output: 10 calls plus 1 retry, about 19.6k input and 10.7k output tokens on gemini-3.6-flash.
- **9a, snapshot override: reverted.** Prompted by case_33, where a 06-18 screenshot showing `export_1260` outranks a 07-03 export inventory that lacks it. Letting the newer snapshot win breaks sample cases 05 and 06: gold treats a row that survives in a replica as present even when a later snapshot omits it. Recall falls from 0.927 to 0.896 and status from 32 to 30, so the record-level fact keeps precedence.

## Remaining sample losses after iteration 8

14 gold steps are still missed and there are no extra steps:

- 6 identifier-less analytics rows (cases 03, 21, 25–28);
- 5 accounts still contested in their time window (cases 18, 23, 28, 29, 32);
- 2 security audit records that appear in no participant data (`audit_record_6645`, `audit_record_5569`);
- `account_004` (case_04), which appears in no participant data either.
