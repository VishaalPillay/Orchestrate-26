# Evaluation report

All numbers are on the 32 public sample cases (`dataset/sample_deletion_requests.csv` against `dataset/sample_output.csv`), produced by:

```text
python code/main.py --mode deterministic --split all
python code/evaluation/score.py --diff
```

Current (iteration 14): step P 1.000 / R 0.927 / F1 0.962, status 32/32, evidence 179/179 on matched steps, 20/32 exact sequences. Evaluation set: 518 rows pass the validator (354 INCOMPLETE, 153 COMPLETE, 11 NOT_APPLICABLE, of which 10 come from intake and 1 is a fallback).

Step metrics match steps as a multiset on `(phase, system_id, resource_id, operation, state)`. Exact matches mean the whole ordered `steps_json`, evidence included, equals gold. The committed `output.csv` comes from `--mode live` and is reproduced byte for byte by `--mode replay` from `code/.cache/llm/`. Model stages: screenshot perception, LLM intake and the category fallback. `--mode deterministic` (rules only, cached perception) differs in 1 evaluation row and scores the same on samples.

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
| 9a | Newer snapshot overrides older record fact | Experiment: a strictly newer non-vacuous covering snapshot marks a DELETE-rule record absent | 1.000 / 0.896 / 0.945 | 30/32 (0.938) | n/a | Reverted |
| 9b | Perception model and prompt fix | `PERCEPTION_MODEL` moved from gemini-3.8-flash (quota exhausted) to gemini-3.6-flash, since gemini-3.7-flash was also over quota and a one-call probe showed 3.6 has quota and accepts images. Resource-id rules added to the prompt; a misplaced but unique visible id is adopted in grounding | 1.000 / 0.927 / 0.962 | 32/32 (1.000) | 20/32 | Kept |
| 10 | Burden of proof for guards | A flow or archive that exists at `audit_at` but has no evidenced disabled or blocked state is an OPEN risk citing its existence fact | 1.000 / 0.927 / 0.962 | 32/32 (1.000) | 20/32 | Kept |
| 11 | LLM intake + hybrid decision | gemini-3.5-flash-lite, 20 requests per call, fenced as data, verbatim evidence_span validated in code; rules, LLM and CLASSIFY_REQUEST corroboration combined by `decide_intake` | 1.000 / 0.927 / 0.962 | 32/32 (1.000) | 20/32 | Kept |
| 11a | LLM safety branch without a negation check | First version let any validated non-deletion span win a disagreement | would have been 26/32: 6 sample deletion audits (10, 21, 26, 27, 28, 30) turned NOT_APPLICABLE on gpt-oss-120b answers | n/a | n/a | Fixed before use |
| 12 | LLM category fallback | CATEGORY_MODEL for names the static map does not cover; masked examples, 15 categories + rule text, answer validated against the list | 1.000 / 0.927 / 0.962 | 32/32 (1.000) | 20/32 | Kept |
| 12a | LLM-only categories for the inferred names | `PCDA_DISABLE_INFERRED=1`: the LLM must categorize case_10 and case_12's event names | 0.989 / 0.896 / 0.940 | 30/32 (0.938) | 18/32 | Rejected (static map stays primary) |
| 13 | Skeptic agent | Tool-calling loop (gemini-3.5-flash-lite, Groq gpt-oss-120b fallback) over a risk-ordered queue, 6 read-only tools, code-side gate, hard call budget (sample 60, test 120) | 1.000 / 0.927 / 0.962 | 32/32 (1.000) | 20/32 | Kept as a no-change guard; no effect on `output.csv` (see below) |
| 14 | Ownership exclusivity in attribution | The model caught 0 of 11 wrong picks in the negative control and the model-free gate found 75 owner-conflicted picks, so that gate check became deterministic: an activity_temporal or activity_joint pick is refused if an ownership-eligible fact ties the resource to another user, or if two different subjects would receive it | 1.000 / 0.927 / 0.962 | 32/32 (1.000) | 20/32 | Kept (36 test verdicts INCOMPLETE -> COMPLETE) |

### Notes per iteration

- **5, joint accounts.** On samples the rule accepted 11 accounts and all 11 were correct; 6 of them were new beyond `activity_temporal`. On the evaluation set it assigns 120 accounts and refuses 4 contested cases. Two evaluation picks sit far outside the sample timing (case_45 at −2086 min, case_493 at −131 min from `reported_at`); every pick is flagged for the skeptic.
- **6, categories.** These names, and case_12's `contact_lookup_completed` + `email_opt_in`, are listed in `category.INFERRED_FROM_EVIDENCE` with their justification. A later run will remove them to test whether the LLM category fallback recovers them.
- **7, joint analytics: caution.** On samples the rule accepted 1 row (case_04 `analytics_901`) and it was correct, so precision is 1.000 and the rule is kept. Its recall is limited: 3 of the 7 gold analytics rows are SUCCESS rows on NOT_STARTED cases (25, 26, 28), which the result-matching rule excludes by design. The weakness is that "no analytics link" does not mean the subject has an analytics record. In 3 sample cases with no analytics in gold (01, 14, 31), the rule ended with a single candidate row and was stopped only because another case contested it. On the evaluation set it assigns 40 rows, and their precision is not supported by the sample evidence the way the account rule's is.
- **8, fallback only: decision.** One correct pick against three near-false-positives does not justify 40 evaluation assignments. The pass is kept only where the alternative is a case with no provable obligation, which can only be written as the NOT_APPLICABLE fallback. There a weak link costs little and a correct one turns the case into a scorable row. On samples this is exactly case_04, so the scores do not change. On the evaluation set it affects 0 cases: none of the 6 fallback cases (39, 50, 53, 55, 61, 63) has a single analytics candidate. Compared with iteration 7, the steps of 14 evaluation rows revert and no status changes.

- **9, perception.** No sample gold step cites a screenshot, so sample scores are unchanged; the check that matters is the manual review. I compared every PNG with its transcription record by record. There were **no hallucinated records**: no invented `tombstone_17` on the reference-registry screen, no `object_19` among the storage file names, and no restore state invented for backups that show none. There were **2 missed records**. On evidence_19 the model used `user_20` as the id of `analytics_7438`, and on evidence_28 it used ticket number `CS-5034` and filed `support_1034` as a field. Grounding dropped both records, so nothing wrong entered the store. Grounded records per image: 04: 1, 09: 2, 14: 2, 19: 0, 23: 4, 28: 0, 32: 4, 37: 6, 42: 0, 46: 0, 60: 1. None of `audit_record_6645`, `audit_record_5569`, `account_004` or `tombstone_440` appears on any screen. On the evaluation set the NOT_APPLICABLE fallbacks drop from 6 to 4 (cases 39 and 63 now get INCOMPLETE). 7 rows change in total. A prompt fix for the two misses (resource_id format rules; user_N, ticket, order and file names are never ids) recovered `support_9538` on the one image it reached, but Gemini's quota ran out before the other 10 were re-transcribed. The fix was applied in 9b with a different model.
- **9a, snapshot override: reverted.** Prompted by case_33, where a 06-18 screenshot showing `export_1260` outranks a 07-03 export inventory that lacks it. Letting the newer snapshot win breaks sample cases 05 and 06: gold treats a row that survives in a replica as present even when a later snapshot omits it. Recall falls from 0.927 to 0.896 and status from 32 to 30, so the record-level fact keeps precedence.
- **9b, model switch.** Compared again record by record against the screenshots: all 3 first-pass misses are recovered (`analytics_7438`, `support_1034`, `support_9538`) and there are no hallucinated records. On evidence_60, gemini-3.6-flash put `marketing_001` among the identifiers instead of in resource_id; grounding adopts it because it is the only id-shaped value transcribed for that record (two candidates would be dropped, never guessed). Records per image: 04: 2, 09: 2, 14: 2, 19: 1, 23: 4, 28: 1, 32: 4, 37: 6, 42: 0, 46: 0, 60: 1. Evaluation-set fallbacks fall from 4 to 2 (cases 50 and 53 become INCOMPLETE). The remaining two are genuine unknowns: case_55's backup shows no restore setting and case_61's `object_19` is on no screen. Usage for the run that produced this output: 10 calls plus 1 retry, about 19.6k input and 10.7k output tokens on gemini-3.6-flash.
- **10, burden of proof.** The spec says COMPLETE requires that no sync or restore path can recreate the data, so missing proof of safety is not safety. On samples the rule never fires: every sample flow and backup has an evidenced state, which leaves the scores unchanged but means the samples neither confirm nor contradict it. On the evaluation set it changes one row: case_55 goes from NOT_APPLICABLE to INCOMPLETE, with REPORT_RESTORE_RISK on `backup_9287` citing evidence_32 (archive visible on screen, no restore setting). case_61 is unchanged and is now the only fallback: `object_19` has activity-catalog rows but no fact, so its existence is not evidenced, and its category (`object_storage`) is unknown. **Flip audit** (all verdicts compared with the run before this rule): 1 flip, case_55. Its cited presence is the evidence_32 screenshot (captured 04-29 09:00 before audit_at 04-30 11:00). Checked by eye, that screen has no restore field at all. The only state fact for `backup_9287` anywhere is evidence_01 `restore_candidate_evaluated eligible=true deletion_block=false` (ACTIVE), observed 09-15, five months after audit_at. A raw search for "9287" finds no other form of the id. Classification: (a) no state proof at or before audit_at, and (c) screenshot-only presence; no parser bug (b). The later ACTIVE state agrees with the INCOMPLETE verdict.

- **11, LLM intake.** `decide_intake`: no usable LLM answer means the rules decide. When the two agree, the case is done. When they disagree, it is flagged `intake_conflict:llm`, and a CLASSIFY_REQUEST non-deletion row on the subject's resources decides. Otherwise an LLM non-deletion answer wins only if its validated span is itself a negation. A rule guard is never overridden by an LLM deletion answer. On samples the one disagreement (case_25, "tell me whether the account … have already been removed") stays with the rules, which matches gold. All 550 answers from gemini-3.5-flash-lite passed span validation.
- **11a, bug found and fixed.** The first run sent every batch to the Groq fallback: gemini-3.5-flash-lite rejects `reasoning_effort: "none"` with HTTP 400. The client now retries once at `"low"` and remembers that per model. Groq's gpt-oss-120b classified 6 sample deletion audits ("Can you verify that my account was deleted?") as non-deletion. Without a negation check the safety branch would have made all 6 NOT_APPLICABLE. The rule now requires a negating span, as specified. Those Groq answers had been cached under the Gemini key and were purged before the comparison.
- **12, category fallback.** The model is called only when no static name decides a resource. It runs before the activity-target fallback, never in deterministic mode, and it is cached per name. On the evaluation set it mapped the screenshot-only `screen:Account events` records (`audit_record_001`, `002`, `004`, `005` and `006`, case_45) to SECURITY_AUDIT_LOG. That is the one row where live output differs from deterministic.
- **12a, LLM-only for inferred names: rejected.** The model put both case_10's `contact_query_completed` and case_12's `contact_lookup_completed` (+`email_opt_in`) under PRODUCT_TELEMETRY ("operational events … function as telemetry"). Gold implies SECONDARY_IDENTIFIER and MARKETING_PROFILE. The static map stays primary.

## Skeptic agent (iteration 13)

The skeptic can only propose; `skeptic_gate.py` checks each claim against the stores and the solver solves the case again. Reproduce: `python code/main.py --mode live --split sample` (then `--split test`), `python code/tools/skeptic_report.py`, `python code/tools/skeptic_control.py`, `python code/tools/skeptic_sweep.py`.

| Measurement | Samples | Test (final configuration) |
|---|---|---|
| Queue / investigated / never reached | 21 / 16 / 6 | 363 / 33 / 330 |
| Model calls (budget) | 60 (60) | 105 (120) |
| Tool calls | 45 | 75 |
| Tokens in / out | 133,861 / 1,853 | 208,701 / 3,995 |
| CONFIRM / CHALLENGE / PROPOSE | 15 / 0 / 0 (1 unfinished, discarded) | 30 / 0 / 0 (3 provider errors) |
| Gate: accepted / rejected | 0 / 0 | 0 / 0 |
| Hallucinated fact_ids; cited but never shown | 0; 0 | 0; 0 |
| Verdicts / steps changed | 0 / 0 | 0 / 0 |
| Step precision, recall, status | 1.000, 0.927, 32/32 | n/a |

The test run stopped early: gemini-3.5-flash-lite's free-tier limit of 500 requests a day was reached, Groq's fallback failed three times, and the circuit breaker ended the queue. An earlier test run with the first prompt (JSON-in-text answer, no `submit_verdict` tool) investigated 35 targets on 120 calls (225,293 / 4,131 tokens) and also confirmed all 34 it finished. In both runs case_45 (pick 35 h from `reported_at`), case_493 and case_450 were confirmed.

**Negative control** (`skeptic_control.py`: each sample account pick replaced by the next case's real pick, 11 cases): gemini-3.5-flash-lite confirmed 10 and challenged 1 (rejected by the gate: row outside the other request's window), so it caught **0 of 11** wrong picks. The gate would have accepted the perfect challenge in 4 of the 11. Groq gpt-oss-120b finished 3 cases (all CONFIRM) and failed 8 with provider errors (it emitted its answer as a call to a tool named JSON or commentary, plus 8K tokens-per-minute 429s). With these models the skeptic is a rubber stamp: its sample result shows it breaks nothing, not that it finds errors.

**Gate sweep** (`skeptic_sweep.py`, no model): what the gate itself accepts when run over every candidate.

| | Samples | Test |
|---|---|---|
| Activity picks | 12 | 207 |
| Picks the gate would remove | 0 | 80 (163 OTHER_SUBJECT claims on the resource's own record, 5 OTHER_REQUEST) |
| Reachable PROPOSE | 0 | 0 |
| Effect of removing all of them | 0 verdicts, 0 steps | 36 verdicts, 359 steps, 72 cases |

The 80 are activity_temporal picks (search, export, support and similar resources chosen as the only deletion_worker row near `reported_at`) whose own record carries another user's identifiers: in case_107 and case_108 the same `search_1752` is picked for two different people, and its log records resolve to a third user. The samples show none, so the sample precision of 1.000 does not exercise it. This exceeds the blast-radius limit (10 verdicts, 25 steps), so none of it was applied; `code/.cache/runs/skeptic_audit_sweep.md` lists every removal with its cited row, the gate checks and the before/after steps.

The queue now ranks picks owned by another user first (87 test targets). That test run was not done (decision: no further model calls on test). The skeptic therefore runs on the samples only (cached), and test cases are flagged `skeptic_skipped:budget`, so `--mode replay --split all` reproduces the live output byte for byte. On test the skeptic changed 0 of its 33 investigations (30 CONFIRM, 3 provider errors), so skipping it does not alter the output. The gate check the model could not use was promoted into deterministic attribution (iteration 14).

### Iteration 14: ownership exclusivity

`identity.conflicting_owner` and `pipeline.find_shared_picks`. An activity-derived pick (activity_temporal or activity_joint, any system) is refused when
(1) an ownership-eligible fact (a record of the resource itself, or one that names it as its `source_resource_id`; never table coverage or an absence) has identifiers that resolve, through the IdentityClosure and so under every historical identifier, only to users other than the subject, or
(2) the same resource would be picked for two different subjects, over all 550 requests. Identifier-based links are untouched. Each refusal is flagged `activity_pick_refused:<resource>:<fact_id>:<user>` (clause 1) or `activity_pick_refused:<resource>:<activity_id>:shared_by_<cases>` (clause 2).

Spot check before applying (`code/tools/ownership_spotcheck.py`, seed 20261025): 75 of 207 test picks and 0 of 12 sample picks are refused by clause 1. 10 of the 75 drawn at random plus case_107 and case_108 were all confirmed: the conflicting fact's identifiers resolve to one other user, that user shares no identifier with the subject (no closure conflict exists in the data), and no identifier of the subject is on the fact. Clause 2 refuses 12 account picks in 12 test cases (a total of 87 refused picks in 81 cases).

Measured against the committed output of the iteration-13 code (live mode):

| | Samples | Test |
|---|---|---|
| Step P / R / F1, status | 1.000 / 0.927 / 0.962, 32/32 (unchanged) | n/a |
| Picks refused | 0 | 87 picks in 81 cases (clause 1: 75 picks in 69 cases; clause 2: 12 picks in 12 cases) |
| Verdict changes | 0 | 36, all INCOMPLETE -> COMPLETE |
| Steps removed / added | 0 / 0 | 287 / 81 |
| Removed by operation | | REPORT_REMAINING_RECORD 69, DELETE_RECORD 69, VERIFY_RECORD_ABSENT 107, VERIFY_RECORD_PRESENT 36, REPORT_EXPOSED_PII 2, REDACT_RECORD 2, VERIFY_RECORD_REDACTED 2 |
| Added by operation | | VERIFY_RECORD_PRESENT 36, VERIFY_RECORD_ABSENT 24, VERIFY_RECORD_REDACTED 7, VERIFY_DATA_FLOW_DISABLED 7, VERIFY_RESTORE_BLOCKED 7 |
| New NOT_APPLICABLE fallbacks | | 0 (still the 10 from intake plus case_61) |

The added steps are the evidence-citing verifications that a COMPLETE case carries and an INCOMPLETE one does not. Account steps changed in 16 cases, every one of them a case whose own account pick was refused: 14 lost it and 2 got a different account from the joint pass (case_206: account_1065 -> account_4522; case_328: account_8264 -> account_206), which now sees them as open. No case without a refusal changed its accounts.

## Model and strategy comparison

### a. Category fallback (samples)

| Strategy | case_10 `contact_query_completed` | case_12 `contact_lookup_completed` + `email_opt_in` | Step P / R / F1 | Status | Exact |
|---|---|---|---|---|---|
| Static only (deterministic) | SECONDARY_IDENTIFIER (inferred list) | MARKETING_PROFILE (inferred list) | 1.000 / 0.927 / 0.962 | 32/32 | 20/32 |
| LLM only for those names (`PCDA_DISABLE_INFERRED=1`, gemini-3.5-flash-lite) | PRODUCT_TELEMETRY ✗ | PRODUCT_TELEMETRY ✗ | 0.989 / 0.896 / 0.940 | 30/32 | 18/32 |
| Combined (static first, LLM for unmapped names) | SECONDARY_IDENTIFIER | MARKETING_PROFILE | 1.000 / 0.927 / 0.962 | 32/32 | 20/32 |

The LLM recovers neither name. Its rationales describe the record shape ("operational events … telemetry") rather than what the event says: a contact-index lookup, a marketing opt-in. The combined strategy equals static-only on samples, because no sample name is unmapped. Its value shows on the evaluation set (case_45, above). The LLM-only variant cost 2 calls (1,115 input and 69 output tokens).

### b. Intake model comparison (all 550 requests, 28 batches of 20)

| Model | Answered (span valid) | Discarded | Agree with rules | Disagree | request_kind agree | Calls | Input tokens | Output tokens |
|---|---|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite (`reasoning_effort` low) | 550 | 0 | 549 | 1 (case_25) | 548 | 28 | 40,194 | 50,752 |
| openai/gpt-oss-120b via Groq (fallback, first run) | 544 | 6 (spans shortened with "…") | 538 | 6 (cases 10, 21, 26, 27, 28, 30) | n/a | 28 | 39,752 | 46,352 |
| gemini-3.6-flash | 0 | 550 | n/a | n/a | n/a | 0 (84 attempts, all HTTP 429 quota) | 0 | 0 |

gemini-3.6-flash could not be measured today: its free-tier quota was exhausted by the perception runs. Re-run it with `python code/tools/intake_compare.py --model gemini-3.6-flash` after the quota resets. Every disagreement for either model that answered is a sample deletion audit worded as a check ("verify", "confirm", "tell me whether … removed"). gemini-3.5-flash-lite is the configured INTAKE_MODEL. It is the only one with zero discarded answers and a single disagreement, and the decision logic keeps that disagreement with the rules.

### Live, replay and deterministic

- **Live** (`--mode live --split all`): the validator passes, with 2 new calls (category fallback); intake and perception came from the cache.
- **Replay** (`--mode replay --split all`): `output.csv` and the sample output are byte-identical to the live run, with 0 provider calls and 41 cache hits.
- **Live vs deterministic:** 1 of 518 rows differs. case_45 stays COMPLETE and gains 5 `VERIFY_RECORD_PRESENT` steps for `audit_record_001`, `002`, `004`, `005` and `006`, which only the LLM category fallback can categorize. Samples score identically in all three modes.

## Remaining sample losses after iteration 12

14 gold steps are still missed and there are no extra steps:

- 6 identifier-less analytics rows (cases 03, 21, 25–28);
- 5 accounts still contested in their time window (cases 18, 23, 28, 29, 32);
- 2 security audit records that appear in no participant data (`audit_record_6645`, `audit_record_5569`);
- `account_004` (case_04), which appears in no participant data either.

## Metrics

Produced by `python code/evaluation/main.py --write` after `python code/main.py --mode deterministic --split all`.
Steps are matched as a multiset on (phase, system_id, resource_id, operation, state) for precision and recall.
Field accuracy is given two ways. **Positional** compares step i of the prediction with step i of gold over the
longer list, so one missing step shifts and penalizes every field after it; it measures how close the serialized
sequence is. **Step-aligned** first pairs steps by (phase, resource_id), preferring the same operation, and compares
fields only over those pairs; it measures whether a step that was found is right in each field, independent of
missing or extra steps (phase and resource_id are 1.000 by construction). Edit distance is Levenshtein over whole
steps (all six fields), divided by the longer length.

| Metric | Samples (32) | Custom overlays (7) |
|---|---|---|
| Cases | 32 | 7 |
| Status accuracy | 32/32 (1.000) | 7/7 (1.000) |
| Steps (gold / predicted / matched) | 193 / 179 / 179 | 31 / 31 / 31 |
| Step precision / recall / F1 | 1.000 / 0.927 / 0.962 | 1.000 / 1.000 / 1.000 |
| Field accuracy, positional: phase | 0.927 | 1.000 |
| Field accuracy, positional: system_id | 0.803 | 1.000 |
| Field accuracy, positional: resource_id | 0.803 | 1.000 |
| Field accuracy, positional: operation | 0.824 | 1.000 |
| Field accuracy, positional: state | 0.824 | 1.000 |
| Field accuracy, positional: evidence_id | 0.922 | 1.000 |
| Step-aligned pairs | 179 | 31 |
| Field accuracy, step-aligned: phase | 1.000 | 1.000 |
| Field accuracy, step-aligned: system_id | 1.000 | 1.000 |
| Field accuracy, step-aligned: resource_id | 1.000 | 1.000 |
| Field accuracy, step-aligned: operation | 1.000 | 1.000 |
| Field accuracy, step-aligned: state | 1.000 | 1.000 |
| Field accuracy, step-aligned: evidence_id | 1.000 | 1.000 |
| Sequence accuracy (exact ordered steps) | 20/32 (0.625) | 7/7 (1.000) |
| Mean normalized edit distance | 0.088 | 0.000 |

### Per-case diff: samples

```text
case_03: gold COMPLETE / pred COMPLETE, matched 2/3, extra 0
    - VERI VERIFY_RECORD_ANONYMIZED   analytics_platform/analytics_9406 ANONYMIZED ev=evidence_94
case_04: gold COMPLETE / pred COMPLETE, matched 1/2, extra 0
    - VERI VERIFY_RECORD_ABSENT       account_service/account_004 ABSENT ev=evidence_54
case_18: gold INCOMPLETE / pred INCOMPLETE, matched 12/14, extra 0
    - VERI VERIFY_RECORD_ABSENT       account_service/account_5521 ABSENT ev=-
    - VERI VERIFY_RECORD_PRESENT      security_service/audit_record_6645 PRESENT ev=-
case_20: gold INCOMPLETE / pred INCOMPLETE, matched 7/8, extra 0
    - VERI VERIFY_RECORD_PRESENT      security_service/audit_record_5569 PRESENT ev=-
case_21: gold COMPLETE / pred COMPLETE, matched 2/3, extra 0
    - VERI VERIFY_RECORD_ANONYMIZED   analytics_platform/analytics_5335 ANONYMIZED ev=evidence_92
case_23: gold INCOMPLETE / pred INCOMPLETE, matched 7/8, extra 0
    - VERI VERIFY_RECORD_ABSENT       account_service/account_410 ABSENT ev=-
case_25: gold COMPLETE / pred COMPLETE, matched 2/3, extra 0
    - VERI VERIFY_RECORD_ANONYMIZED   analytics_platform/analytics_1895 ANONYMIZED ev=evidence_92
case_26: gold COMPLETE / pred COMPLETE, matched 5/6, extra 0
    - VERI VERIFY_RECORD_ANONYMIZED   analytics_platform/analytics_4566 ANONYMIZED ev=evidence_92
case_27: gold COMPLETE / pred COMPLETE, matched 5/6, extra 0
    - VERI VERIFY_RECORD_ANONYMIZED   analytics_platform/analytics_4574 ANONYMIZED ev=evidence_92
case_28: gold COMPLETE / pred COMPLETE, matched 4/6, extra 0
    - VERI VERIFY_RECORD_ABSENT       account_service/account_5482 ABSENT ev=evidence_92
    - VERI VERIFY_RECORD_ANONYMIZED   analytics_platform/analytics_6077 ANONYMIZED ev=evidence_92
case_29: gold INCOMPLETE / pred INCOMPLETE, matched 13/14, extra 0
    - VERI VERIFY_RECORD_ABSENT       account_service/account_7012 ABSENT ev=-
case_32: gold INCOMPLETE / pred INCOMPLETE, matched 4/5, extra 0
    - VERI VERIFY_RECORD_ABSENT       account_service/account_8858 ABSENT ev=-
```

### Per-case diff: custom overlays

```text
all custom cases exact
```

## Custom evaluation cases

Each case copies `dataset/` to a temporary directory, applies one targeted mutation, and runs the real pipeline on
one case (`main.py --dataset-dir <overlay> --case <id> --out <file>`). The expected verdict and steps are literal
in `code/evaluation/custom_cases.py`. Base cases are public samples the pipeline already matches exactly, so an
unchanged expectation equals that sample's published answer. Run them with `python code/evaluation/custom_cases.py`.

| # | Base | Mutation | Tests | Why | Expected | Result |
|---|---|---|---|---|---|---|
| 1 non-deletion request | case_05 | request_text -> 'Please remove me from marketing lists only; keep my account.' | Intake recognizes a marketing opt-out that explicitly keeps the account. | A request that does not authorize deletion must be NOT_APPLICABLE with no steps, even though the case's evidence still shows a remaining account (case_05 is INCOMPLETE as a deletion). | NOT_APPLICABLE, 0 steps | pass (NOT_APPLICABLE) |
| 2 post-audit deletion | case_24 | new ledger evidence_9001 captured and recorded 2026-09-16 (audit_at + 1 day): search_5577 NOT_FOUND | Evidence observed after audit_at cannot change the answer. | The spec fixes the answer at audit_at; a later 'deleted' observation must neither discharge the remaining search entry nor be cited. | INCOMPLETE, 7 steps | pass (INCOMPLETE) |
| 3 replica drift | case_16 | evidence_05: delete the customer_data_primary INSERT for search_1007, keep the replica row | A row deleted from the primary but still in the read replica is a remaining record. | Deletion workflows often clean the primary only; the dataset models replica drift explicitly. | INCOMPLETE, 7 steps | pass (INCOMPLETE) |
| 4 decoy identity | case_06 | new ledger evidence_9002 (2026-09-05, inside the request window): search_9999 FOUND for jonas.bergg@outlook.com | A near-identical email (one extra character) on another resource is not the subject's. | Identity matching must be exact; fuzzy matching would delete someone else's data. | INCOMPLETE, 4 steps; never search_9999 | pass (INCOMPLETE) |
| 5a historical email in the request, uppercased | case_10 | request email -> NADIA.PETRESCU@YAHOO.COM (user_10's second email, audit_121/audit_238) | A request filed under an intermediate historical email, in upper case, resolves to the same subject. | Requests may come from any past address; normalization and the audit-log closure must both hold. | INCOMPLETE, 4 steps | pass (INCOMPLETE) |
| 5b historical email in evidence, uppercased | case_10 | evidence_61: lookup_key=nadia.ionescu@yahoo.com -> NADIA.IONESCU@YAHOO.COM | Evidence that names the subject only by an old email, in upper case, still attributes the resource. | Legacy systems keep old identifiers in arbitrary case; support_002's only link is that old email. | INCOMPLETE, 4 steps | pass (INCOMPLETE) |
| 6 tombstone with PII | case_31 | evidence_51: tombstone_440 name -> 'Ada Eze', email -> a.eze08@gmail.com | A tombstone that regains direct identifiers is exposed PII to redact, not a record to delete. | ACCOUNT_TOMBSTONE is REDACT: keep the stable user reference, remove name/email/phone/hash. | INCOMPLETE, 5 steps | pass (INCOMPLETE) |

Every mutation was confirmed to reach the fact store, so no pass is vacuous:
- case 2's post-audit ABSENCE is ingested and then excluded by `as_of(audit_at)`;
- case 3 leaves only the replica row of `search_1007`;
- case 4's `search_9999` exists with an email that resolves to no user;
- case 5b's uppercased key normalizes to `user_10`.
