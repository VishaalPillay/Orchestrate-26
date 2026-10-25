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

### Notes per iteration

- **5, joint accounts.** On samples the rule accepted 11 accounts and all 11 were correct; 6 of them were new beyond `activity_temporal`. On the evaluation set it assigns 120 accounts and refuses 4 contested cases. Two evaluation picks sit far outside the sample timing (case_45 at −2086 min, case_493 at −131 min from `reported_at`); every pick is flagged for the skeptic.
- **6, categories.** These names, and case_12's `contact_lookup_completed` + `email_opt_in`, are listed in `category.INFERRED_FROM_EVIDENCE` with their justification. A later run will remove them to test whether the LLM category fallback recovers them.
- **7, joint analytics: caution.** On samples the rule accepted 1 row (case_04 `analytics_901`) and it was correct, so precision is 1.000 and the rule is kept. Its recall is limited: 3 of the 7 gold analytics rows are SUCCESS rows on NOT_STARTED cases (25, 26, 28), which the result-matching rule excludes by design. The weakness is that "no analytics link" does not mean the subject has an analytics record. In 3 sample cases with no analytics in gold (01, 14, 31), the rule ended with a single candidate row and was stopped only because another case contested it. On the evaluation set it assigns 40 rows, and their precision is not supported by the sample evidence the way the account rule's is.
- **8, fallback only: decision.** One correct pick against three near-false-positives does not justify 40 evaluation assignments. The pass is kept only where the alternative is a case with no provable obligation, which can only be written as the NOT_APPLICABLE fallback. There a weak link costs little and a correct one turns the case into a scorable row. On samples this is exactly case_04, so the scores do not change. On the evaluation set it affects 0 cases: none of the 6 fallback cases (39, 50, 53, 55, 61, 63) has a single analytics candidate. Compared with iteration 7, the steps of 14 evaluation rows revert and no status changes.

## Remaining sample losses after iteration 8

14 gold steps are still missed and there are no extra steps:

- 6 identifier-less analytics rows (cases 03, 21, 25–28);
- 5 accounts still contested in their time window (cases 18, 23, 28, 29, 32);
- 2 security audit records that appear in no participant data (`audit_record_6645`, `audit_record_5569`);
- `account_004` (case_04), which appears in no participant data either.
