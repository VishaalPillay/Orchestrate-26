# Evaluation report

All numbers are on the 32 public sample cases (`dataset/sample_deletion_requests.csv` against `dataset/sample_output.csv`), produced by:

```text
python code/main.py --mode deterministic --split all
python code/evaluation/score.py --diff
```

Current (iteration 23): step P 1.000 / R 0.984 / F1 0.992, status 32/32, evidence 190/190 on matched steps, 29/32 exact sequences. Evaluation set: 518 rows pass the validator (355 INCOMPLETE, 153 COMPLETE, 10 NOT_APPLICABLE, all 10 from intake; the flagged no-provable-obligation fallback of iteration 22 applies to 0 rows).

Step metrics match steps as a multiset on `(phase, system_id, resource_id, operation, state)`. Exact matches mean the whole ordered `steps_json`, evidence included, equals gold. The committed `output.csv` is written by `--mode replay` from the cache of the last live run (`code/.cache/llm/`): iterations 15 to 19 added no model call, so replay has zero cache misses, and the sample skeptic replays 60 of 60 calls. A confirming `--mode live` run after these iterations is still to be done; it should make 0 provider calls. Model stages: screenshot perception, LLM intake, the category fallback, and the skeptic on the samples. `--mode deterministic` (rules only, cached perception) differs in 1 evaluation row (case_45) and scores the same on samples.

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
| 15 | Global forced assignment | Joint passes become one constraint propagation over all 550 requests (`identity.propagate`): a row serves one request, a resource one subject, account results match `reported_status`, exclusivity (`conflicting_owner`) and identifier ownership are hard constraints inside the loop, activity_temporal picks are held as forced singletons; simultaneous rounds, no time tie-breaks. Tried a two-sided analytics pass (row also forced to the request, window `[requested_at, audit_at]`) | 1.000 / 0.927 / 0.962 | 32/32 (1.000) | 20/32 | Kept as the formulation (no output change on samples or test; order-independent under 3 shuffles). Analytics stays fallback-only: two-sided forcing assigned 0 rows on either split and is not wired in |
| 16 | Analytics without result-matching | Data check (`code/tools/status_result_check.py`, identifier-attributed resources only, no gold): account row results track `reported_status` in 252 of 253 cases with a row (every NOT_STARTED row is FAILED), analytics results do not (COMPLETE cases: 21 FAILED, 4 PARTIAL, 3 SUCCESS). Experiment: both analytics passes drop result-matching | 1.000 / 0.922 / 0.960 | 31/32 (0.969) | 20/32 | Reverted: case_04 loses `analytics_901` (more candidates, nothing forced); test unchanged; two-sided still assigns 0 |
| 17 | Test-side leak audit + stable user_id | `code/tools/leak_audit.py` (every flag and silent rejection by type, test vs samples) and `code/tools/invariants.py` (citation, shared resources, account verification, audit-record retention, per-system shares). One generic fix: the staleness gate no longer rejects a candidate matched on the subject's own `user_id`, which is never reassigned (the gate exists for recycled emails and phones, the case_17 decoy) | 1.000 / 0.927 / 0.962 | 32/32 (1.000) | 20/32 | Kept (1 test row: case_34 gains VERIFY_RECORD_PRESENT `audit_record_3753`) |
| 18 | Nearest-row tie-breaker (`activity_ranked`) | Experiment reversing "never break ties by time": for requests with no account, the feasible row nearest `reported_at`, one global pass (largest rank-1/rank-2 margin first, exclusivity kept), adding only the account VERIFY step (verdict changes asserted 0). Validation on forced picks (`code/tools/rank_validation.py`, no gold): rank 1 = forced pick 129/133 (0.970), but only 23 had 2+ feasible rows: 22/23 | 0.978 / 0.938 / 0.958 | 32/32 (1.000) | n/a | Reverted: F1 fell; 2 of 6 sample picks correct (cases 18, 32); test would gain 135 account steps, 0 verdict changes |
| 19 | Support message vs attachment by content | 8 test support records whose names point to both SUPPORT_MESSAGE and SUPPORT_ATTACHMENT were decided by name order. Now `category.support_by_content`: shared order/agent context (rule_05) -> MESSAGE, else file attributes (rule_06) -> ATTACHMENT, neither -> MESSAGE (reversible) and flagged | 1.000 / 0.927 / 0.962 | 32/32 (1.000) | 20/32 | Kept (2 test rows: case_131, case_179 REDACT -> DELETE; 0 verdict flips) |
| 20 | Run clock + matching for account rows | The deletion worker's rows of one run share a clock: the account row sits in a band before `reported_at`, and every later row of the run has an offset from it whose seconds residue depends only on the row template. `runclock.learn` measures band, horizon and residues from the 238 evaluation requests whose account is attributed by identifiers (no gold), and the account pass keeps only candidate rows inside the band and phase-consistent with the case's own identifier-attributed run rows. The remaining ambiguity is resolved only by edges present in every maximum matching of requests to account rows (`identity.forced_by_matching`) | score.py before 1.000 / 0.927 / 0.962, after 1.000 / 0.953 / 0.976 | 32/32 (1.000) | 20/32 -> 23/32 | Kept. Test diff (`output_diff.py`, committed output vs new): 96 rows changed, +94 account steps in 94 cases, 2 removed (case_45, case_493), 0 verdict changes |
| 21 | Analytics rows through the account row | The same run clock links identifier-less analytics rows: an ANONYMIZE_RECORD analytics row is a candidate for a request with no analytics link by identifiers when it fits the run of that request's account row (template seen in pinned runs, offset inside the template's learned window, learned residue), the resource is not owned by another subject, and propagation leaves it unclaimed by any other request. No result matching. The old unanchored pass stays as the no-provable-obligation fallback (case_04) | score.py before 1.000 / 0.953 / 0.976, after 1.000 / 0.984 / 0.992 | 32/32 (1.000) | 23/32 -> 29/32 | Kept. Test diff (`output_diff.py`, iteration 20 output vs new): 53 rows changed, +53 analytics steps (all `VERIFY_RECORD_ANONYMIZED`), 0 removed, 0 verdict changes |
| 22 | Fallback is INCOMPLETE, not NOT_APPLICABLE | An external audit pointed out that case_61 asks explicitly for deletion but was written NOT_APPLICABLE, which the spec reserves for requests that do not authorize deletion. A request that authorizes deletion with no provable obligation is now INCOMPLETE (completion cannot be proven: the burden-of-proof rule of iteration 10), flagged `fallback_no_provable_obligation`, with findings only for resources with evidenced presence (none here, so `steps_json=[]`). (The validator exception for flagged cases was removed again, see the iteration 23 note) | score.py before 1.000 / 0.984 / 0.992, after 1.000 / 0.984 / 0.992 (samples byte-identical) | 32/32 (1.000) | 29/32 | Kept. Test diff (`output_diff.py`): exactly 1 row changed, case_61 NOT_APPLICABLE -> INCOMPLETE `[]`; 0 steps added or removed elsewhere |
| 23 | File-name join for screenshot records | An audit found a lead on case_61: evidence_46 (captured 2026-02-13 08:00, before `audit_at`) lists `delivery_confirmation_19.pdf` and `activity_4007` (LINK_RESOURCE_IDENTITY) maps `object_19` to that exact name for the subject. Grounding dropped the record (no id-shaped value). A record with no id-shaped value but an exact, case-sensitive file name with extension now takes the resource of the one catalog row that maps a resource to that name (zero or several rows: still dropped); the category comes from the catalog row's own text ("Attachment catalog ... belongs only to" = rule_06) | score.py before 1.000 / 0.984 / 0.992, after 1.000 / 0.984 / 0.992 (samples byte-identical) | 32/32 (1.000) | 29/32 | Kept. Test diff (`output_diff.py`): exactly 1 row changed, case_61 +3 steps; the iteration-22 fallback applies to 0 rows |
| 24 | Validator hardening (external audit of `code.zip`) | For INCOMPLETE rows the validator now requires, per `(system_id, resource_id)`, a matching REMEDIATION for every FINDING (REPORT_REMAINING_RECORD -> DELETE_RECORD; REPORT_EXPOSED_PII -> REDACT_RECORD or ANONYMIZE_RECORD; REPORT_ACTIVE_DATA_FLOW -> DISABLE_DATA_FLOW; REPORT_RESTORE_RISK -> BLOCK_RESTORE) and a matching VERIFICATION for every REMEDIATION. `step_no` must be an `int` (not bool or float) and every step field is type-checked before any lookup, so malformed input gives a ContractViolation, never a TypeError | score.py before 1.000 / 0.984 / 0.992, after 1.000 / 0.984 / 0.992 (samples byte-identical) | 32/32 (1.000) | 29/32 | Kept. The new check was run over `output.csv`, the sample output and `dataset/sample_output.csv` first: 0 violations, so `output.csv` is byte-identical |

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

### Iteration 15: global forced assignment

`identity.propagate` replaces the greedy elimination. Before, exclusivity refusals (`conflicting_owner`, `find_shared_picks`) ran after the joint pass, so a later-refused row could already have eliminated candidates elsewhere. Now every constraint is inside the loop. Each round takes every forced request at once: one subject forced to a resource takes it and the resource leaves every other subject's request; two subjects forced to the same resource are both refused. A round reads a snapshot and only removes, so the result cannot depend on request order. `code/tools/joint_order_check.py` builds every pass in file order and under seeds 11, 2026 and 90210: identical assignments, refusals and refused holds.

| | Samples | Test |
|---|---|---|
| Step P / R / F1, status | 1.000 / 0.927 / 0.962, 32/32 (unchanged) | n/a |
| Account assignments | unchanged | 128 assigned, 139 refused, 12 activity_temporal holds refused as contradictions (the same 12 clause 2 already refused); 0 added, 0 removed, 0 changed |
| Two-sided analytics | 0 assigned | 0 assigned (471 requests: no row is forced on both sides) |
| Rows changed | 0 | 0 (deterministic and replay `output.csv` byte-identical to before) |

The 5 contested sample accounts stay unassigned because the data does not force them: each case keeps 3 to 4 feasible rows (for example case_28 and case_29 share `account_5482` and `account_7012`, alongside `account_5715` and `account_9862`, which no request in either file claims). Rows of people outside the dataset are exactly what a forced-only rule must not resolve. Two-sided analytics with result-matching assigns nothing anywhere, so it failed the bar (at least 2 sample analytics rows gained) and the one-sided analytics pass stays fallback-only. The two-sided pass is kept as `pipeline.TWO_SIDED_ANALYTICS` for the order check, not in `JOINT_PASSES`.

**Failure found while measuring (fixed):** wiring the inert two-sided pass in, and rewording `joint_none` reasons, changed the case flags that the skeptic's `case_context` tool shows. Its cache keys include that text, so replay of the sample skeptic missed (3 ERROR outcomes, then the circuit breaker) even though no output row changed. Restoring the flag text and unwiring the pass brought back 60 of 60 cached calls. Lesson: any change to flag or reason strings must be followed by a replay run, not only a deterministic one.

### Iteration 16: analytics result-match (reverted)

The assumption behind result-matching holds for accounts and fails for analytics. Rows inside `[requested_at, audit_at]` for resources attributed by identifiers, over all 550 requests:

| reported_status | account FAILED | PARTIAL | SUCCESS | analytics FAILED | PARTIAL | SUCCESS |
|---|---|---|---|---|---|---|
| COMPLETE | 0 | 0 | 133 | 21 | 4 | 3 |
| NOT_STARTED | 50 | 0 | 0 | 19 | 1 | 0 |
| PARTIALLY_COMPLETE | 0 | 69 | 1 | 23 | 3 | 3 |

So `reported_status` summarizes the account step only. Dropping result-matching for analytics is still the wrong move for a forced-only rule, because it only widens each request's candidate set: the one-sided fallback pass falls from 41 to 20 test assignments and loses the one correct sample pick (case_04 becomes the NOT_APPLICABLE fallback again), and the two-sided pass stays at 0. Result-matching on analytics is kept as a filter that happens to reduce candidates, and is documented as having no causal basis in the data. The 6 identifier-less sample analytics rows stay missed.

### Iteration 17: test-side leak audit

Deterministic run over all 550 requests, no gold (`code/.cache/runs/leak_audit.md`, `code/.cache/runs/invariants.md`). Leak = what the flag signals: recall (a step is likely missing) or precision (a step is likely wrong).

| Type | Leak | Test (cases) | Samples | Reading |
|---|---|---|---|---|
| joint_none | recall | 983 (459) | 57 | No row forced to the request. Accounts: 139 test requests keep 2+ feasible rows. This is the largest test leak, see invariant 3 |
| unknown_state | recall | 122 (117) | 5 | 116 are SECURITY_AUDIT_LOG records whose only evidence was captured after `audit_at`; all sit in INCOMPLETE cases and are verified with `""`, as gold does on samples (invariant 4: 0 violations) |
| activity_pick_refused | recall if wrong | 87 (81) | 0 | Exclusivity refusals; spot-checked 12 of them in iteration 14, all correct |
| post_audit_rejected | recall | 41 (27) | 0 | Checked: every first observation is a timestamped log line 1 to 210 days after `audit_at` (search entries reappearing after the audit). Correctly excluded; no parser time bug |
| perception_drop | recall | 17 records | 0 | Records with no id-shaped value; the record-by-record screenshot review in 9b found no missed resource |
| category_unknown_name | recall | 16 (11) | 1 | Screen titles; only case_45's 5 screenshot-only audit records (live mode categorizes them through the LLM fallback) and case_61's `object_19` stay without a category |
| category_conflict | precision | 8 (8) | 0 | Every one is SUPPORT_MESSAGE (REDACT) vs SUPPORT_ATTACHMENT (DELETE) on one support id that has both a message and an attachment record. The tie is broken by name order. The samples never exercise it, so there is no measurable basis for a rule: left as is and documented |
| staleness_rejected | recall | 2 (1) | 3 | case_34: `audit_record_3753` linked only by the subject's own `user_id`, 9 days before the request. Fixed (below); the 3 sample rejections are email matches and stay rejected |
| fallback_no_provable_obligation, uncited, unproven_guard | recall / precision | 1, 1, 1 | 0 | case_61 `object_19` and case_46 `tombstone_17` have no fact at all; case_55's burden-of-proof finding was audited in iteration 10 |

Invariants over the output:

| Invariant | Violations |
|---|---|
| 1. FINDING and COMPLETE-verification evidence mentions the resource or is its covering snapshot | 0 |
| 2. A resource in two subjects' steps is identifier-linked in both | 0 |
| 3. Every INCOMPLETE case verifies the account | 98 test, 4 sample (all "no account attributed"). Gold has an account step in 32 of 32 sample cases; our test output has none in 144 of 507 deletion cases (28%), samples 5 of 31 |
| 4. Every SECURITY_AUDIT_LOG obligation gets VERIFY_RECORD_PRESENT | 0 |
| 5. Per-system share of cases with findings, test vs samples (0.5x to 2x) | none outside except identity_service (test 0.01 vs sample 0.03, one sample case) and marketing_platform (test only, below 0.01) |

Invariant 3 is the measurable recall gap the samples understate: about 144 test cases are missing their account steps. Its root cause is accounts that no evidence forces (iteration 15). Assigning them anyway would mean breaking ties by time distance, which is ruled out.

**Fix kept:** the staleness gate now spares a candidate matched on the subject's `user_id`. Samples unchanged, 1 test row changed (case_34 adds `VERIFY_RECORD_PRESENT security_service/audit_record_3753`, evidence `""`, because its only fact is captured before `audit_at` but the case is INCOMPLETE). Not fixed, with reasons: the support category conflicts (no basis), case_45 in deterministic mode (live output already correct), and the 98 missing accounts (not forced).

### Iteration 18: nearest-row tie-breaker (reverted, evidence for abstaining)

The bar was at least 90% agreement on cases whose answer is already known, then a rise in sample F1. The tie-breaker passed the first test and failed the second.

| Validation (forced picks hidden, feasible rows before elimination) | Agree |
|---|---|
| activity_joint, samples / test | 6/6, 121/122 |
| activity_temporal, samples | 2/5 (3 picks lie outside their own feasible set: PARTIALLY_COMPLETE status with a SUCCESS row) |
| All | 129/133 = 0.970 |
| Only cases with 2+ feasible rows | 22/23 = 0.957 |
| By gap of the forced pick: <=15 min / 15-60 min / 1-3 h / >3 h | 2/2, 67/67, 59/60, 1/1 |
| Rank-1/rank-2 margin, agreeing joint picks (n=20) | min 49, median 1424, max 4191 min |
| Margin of the one disagreement | 79 min (inside the agreeing range, so margin cannot gate it) |

Applied to the 6 sample requests that end with no account, it picked 2 correctly (case_18 `account_5521`, case_32 `account_8858`). It missed case_23 (`account_9155` for gold `account_410`), swapped case_28 and case_29 (`account_7012` and `account_5482` are exactly each other's gold), and gave case_04 `account_5661` (gold `account_004` is in no participant file). Step P/R/F1 went 1.000/0.927/0.962 -> 0.978/0.938/0.958. On test it would add 135 VERIFY_RECORD_ABSENT steps with 0 verdict changes. The validation set overstates accuracy because 110 of its 133 cases had a single feasible row, so agreement there is automatic. The real contested cases have several rows close in time, and there the rule is near chance (2/6). Abstaining stays the decision.

### Iteration 20: run clock and matching

**Framing.** Run membership is detected from the worker's own pinned runs (requests whose account is attributed by identifiers), and is used only to link activity claims to a request. It never decides a state: every step it adds is still checked by exclusivity, ownership and `as_of(audit_at)` evidence.

**What the raw rows show.** 539 deletion_worker account DELETE rows against 550 requests (by result: SUCCESS 270, PARTIAL 158, FAILED 111; by `reported_status`: COMPLETE 271, PARTIALLY_COMPLETE 157, NOT_STARTED 122, of which 10 are non-deletion requests), and every row is feasible for at least one request, so rows and requests are close to one to one and the "rows of people outside the dataset" reading was wrong. Learned on the 238 identifier-pinned evaluation runs (`run_log` of `main.py` prints it):

| Learned | Value | Distribution |
|---|---|---|
| Band of the account row | -75.0 .. -43.9 min from `reported_at` | extremes -74.9 / -44.0 min, padded by range / n = 0.13 min |
| Horizon of the run | 3.5 h after the account row | rows per 30-min bin: 0: 5, 1: 51, 2: 16, 3: 113, 4: 117, 5: 44, 6: 2; none beyond 3.1 h |
| Residue (seconds mod 30; folded because every residue's +30 s partner was observed for 90%+ of pairs) | 20-22 s for "final result", connector and restore rows; 9-11 s for "Policy action completed" rows | 348 later run rows; 0 exceptions |

The 5 h horizon of the plan could be learned from pins: it is 3.5 h. Templates with fewer than 30 pinned pairs (restore checks, 9 pairs) are left unconstrained.

**Validation (no gold).** `python code/tools/clock_validation.py`:

| Check | Result |
|---|---|
| Clock learned on even-numbered pinned runs, checked on odd (and the reverse) | true row rejected 0 of 238; other status-matched rows that also pass (0 / 1 / 2 / 3+): 101 / 29 / 5 / 0 and 85 / 16 / 2 / 0 |
| Earlier variants of the same check | no band padding: 2 rejections (band edges); minimum 3 pairs per template with residues mod 60: 8 rejections; these are why the band is padded and the residue period is folded |
| Hide-and-force: every pinned request loses its identifier-attributed account, the real pass runs | 235 correct, 0 wrong, 3 abstained of 238 |
| Order independence (`joint_order_check.py`, 3 shuffles) | identical |

**Samples.** Step P / R / F1 1.000 / 0.953 / 0.976 (184 of 193), 0 extra steps, status 32/32. The five contested accounts (cases 18, 23, 28, 29, 32) are now matched and all five are right. Halves: cases 01-16 73 of 75 (unchanged), cases 17-32 111 of 118 (was 106). The one account still missing is `account_004`, which is in no participant file.

**Test.** 96 rows change against the previous `output.csv`: 94 gain `VERIFY_RECORD_ABSENT` on an account (deletion cases without an account step: 144 to 52 of 507), 2 lose theirs (case_45, whose pick was 35 h before `reported_at`, and case_493, 131 min before): no row in the band is phase-consistent, so the solution now abstains. 0 verdict changes. Invariant 3 (INCOMPLETE case without an account verification, `invariants.py`): 102 to 31 (test 98 to 31, samples 4 to 0). `python code/tools/clock_spotcheck.py` prints raw rows for 10 random new accounts: nine are decided by the phase test (every other in-band row is phase-inconsistent), one (case_395) by the matching rule (the other two in-band rows are held by cases 396 and 397). Of the 52 cases still without an account (31 INCOMPLETE, 21 COMPLETE), counting status-matched rows in the band before the phase test: 8 have none, 6 have exactly one (removed by the phase test, by ownership or because another request holds it) and 38 have two or more. 40 carry `joint_none` with `N candidate rows remain` or `also the only candidate of …`.

**Replay.** The new picks change the sample cases' flags and so the skeptic's cache keys. The sample skeptic was regenerated once with the configured gemini-3.5-flash-lite only (`LLM_PROVIDERS=gemini`, 60 of 60 calls, 28 from the old cache, 15 CONFIRM, 0 changes); `--mode live --split all` then made 0 provider calls and `--mode replay --split all` reproduces `output.csv` and the sample output byte for byte. `test_runclock.py::test_pipeline_never_opens_sample_output_csv` asserts the pipeline never opens `dataset/sample_output.csv`.

### Iteration 21: analytics rows through the account row

**Framing.** As in iteration 20: run membership is detected from the worker's own pinned runs and used only to link activity claims; ownership, exclusivity and `as_of(audit_at)` evidence still govern every step.

**Learned (printed by `main.py`).** Offsets of a pinned run's later rows after its account row, by template, over 348 rows: "Policy action completed" rows 22.5 to 67.8 min (residues 9-11 s mod 30); "final result" rows 84.5 to 179.7 min (residues 20-22 s); connector checks 84.1 to 187.1 min. The windows are disjoint and tight, and the same template means the same timing for every resource type, so a candidate analytics row must match one: this is what separates the competing claimants that blocked the unanchored passes (case_21 and case_23 both fit analytics_5335 by phase alone).

**Validation (no gold).**

| Check | Result |
|---|---|
| Analytics hide-and-link: 72 requests whose analytics is attributed by identifiers and has an ANONYMIZE row lose it; clock learned on the other parity only | 64 correct, 0 wrong, 8 abstained |
| Samples: the 19 gold cases with no analytics step | 0 get one (0 extra steps) |
| Order independence (3 shuffles, anchored and fallback passes) | identical |
| Samples | P 1.000 / R 0.984 / F1 0.992 (190 of 193), status 32/32, 0 extra; halves: cases 01-16 74 of 75, cases 17-32 116 of 118 |
| Test | 53 cases gain `VERIFY_RECORD_ANONYMIZED` (all picks are "Policy action completed" SUCCESS rows, as in the sample misses), 0 verdict changes, nothing removed; analytics steps appear in 25% of deletion cases on test (13 of 32 sample gold cases) |

`python code/tools/clock_spotcheck.py BEFORE AFTER --system analytics_platform` prints raw rows for 10 random picks: every pick has residue 9-11 and an offset of 27-42 min; the other analytics rows near each account row fail the window or the residue (or belong to another subject by identifiers).

**What was not needed.** The planned 2b (a run-template exclusion rule) was not applied: the template windows already separate case_21 from case_23.

**Remaining sample losses: 3** (`audit_record_6645`, `audit_record_5569`, `account_004`), none of which appears in any participant file.

### Iteration 22: the no-provable-obligation fallback

**Why.** Case_61 (`priya.raman@gmail.com`, subject user_07): "Please treat this message as a request for permanent account deletion. Please check the records connected to the account before confirming completion." Intake: deletion (rule and LLM agree). Its only linked resource is `object_19` (CATALOG_LINK, from the attachment-catalog row `activity_4007`). `object_19` has no fact in any evidence file and its only name is the activity target `object_storage`, which maps to no policy category, so no obligation exists (flags: `category_unknown_name`, `category_unknown`). No account row is attributed either (`joint_none`). Nothing is provably present or absent, so the old rule wrote NOT_APPLICABLE, which the spec uses only for requests that do not authorize deletion.

**Rule.** `solver.solve` returns INCOMPLETE with `fallback_no_provable_obligation` when intake authorizes deletion and no obligation is OPEN or DISCHARGED: completion cannot be proven, the burden-of-proof rule. `planner.project` emits findings only for OPEN obligations (resources with evidenced presence), so this path has no steps. `validator.validate_output` first accepted `INCOMPLETE` with `steps_json=[]` only for flagged cases; that exception was removed in the iteration 23 note below. NOT_APPLICABLE is now exclusively an intake outcome.

**Acceptance.** Samples byte-identical (F1 0.992, 32/32, skeptic cache 60 of 60); the test file differs in exactly 1 row (case_61, `[]`); 131 tests, ruff, mypy and the 8 custom cases pass.

### Iteration 23: file-name join for screenshot records

**Lead.** `evidence_46` (`object_storage_browser_20260213.png`, captured 2026-02-13 08:00) is a Nimbus Object Storage listing of `northstar-customer-data / eu-west-1 / support-attachments / 2026 / 02`. Its first record, `delivery_confirmation_19.pdf` (684 KB, v3, uploader support-service), was transcribed with `resource_id` null, so grounding dropped it. `activity_4007` (`LINK_RESOURCE_IDENTITY`, `data_catalog`, SUCCESS, 2026-02-13 08:07:01) says "Attachment catalog confirmed object_19 (delivery_confirmation_19.pdf) belongs only to priya.raman@gmail.com and user_07; no other subject is linked."

**Rule.** In `ground_screen`, when no id-shaped value is found, the values of the record (its `resource_id` slot and its field values) are matched case-sensitively against the file names that LINK_RESOURCE_IDENTITY rows give for a resource (`LineageGraph.file_name_links`, parenthesised names with an extension, for the row's own resource). Exactly one distinct name on the record and exactly one catalog row for it: the record takes that row's resource id (logged as a warning with the activity id). Zero rows, several rows, several names, a different case or no extension: the record stays dropped. Cached perception output only; no model call. The join is recorded, not guessed: it adds a fact for `object_19`, observed 2026-02-12 18:42 / captured 02-13 08:00, both at or before `audit_at` 2026-02-14.

**Category.** Which field decided: the catalog row's own `details` text, not the id (the prefix `object_` and the target system `object_storage` map to no category) and not the screen title (`Nimbus Object Storage`). A LINK_RESOURCE_IDENTITY row starting "Attachment catalog" yields the name `catalog:attachment`, listed in `INFERRED_FROM_EVIDENCE` with its justification: the row names the record an attachment and states rule_06's condition verbatim ("belongs only to the deletion subject"). It is a static decision taken before the LLM fallback, so replay needs no new model call. The `support-attachments` path appears on the screenshot but is not in the cached transcription's fields, so it did not decide anything.

**Effect.** Samples byte-identical (F1 0.992, 32/32, skeptic cache 60 of 60). The test file differs in exactly 1 row: case_61 gains FINDING `REPORT_REMAINING_RECORD` (cited `evidence_46`), REMEDIATION `DELETE_RECORD` and VERIFICATION `VERIFY_RECORD_ABSENT` on `object_storage/object_19`. Of the perception drops, this was the only record with a unique file-name match; 15 records stay dropped (no id and no catalog name). The iteration-22 fallback now applies to 0 rows (kept as a rule for the case it was written for). A catalog-row collision exists in the data: `support_3802` (a different subject's ticket attachment, captured 2026-03-30 in a SQL snapshot) carries the same file name, but it is not a LINK_RESOURCE_IDENTITY row and was captured after `audit_at`, so it is neither a second catalog match nor evidence for this case.

**Iteration 23 note: the validator exception is removed.** `validate_output` now rejects INCOMPLETE with `steps_json=[]` unconditionally (`fallback_cases` is gone). If the solver ever reaches `fallback_no_provable_obligation`, the case fails validation and `main.py` writes nothing, instead of writing an empty INCOMPLETE row. The fallback applies to 0 rows today: the replay log has no `fallback_no_provable_obligation` flag in either split, and `output.csv` and the sample output are byte-identical to the commit before (`e36b1d8`). New tests: `test_incomplete_with_no_steps_is_rejected_unconditionally`, and `test_a_catalog_row_naming_another_user_never_gives_the_resource_to_this_subject` (the file-name join is user-agnostic, but a catalog row that names another user never attributes the resource to this subject; the owner's own request still gets it).

### Iteration 19: support message vs attachment by content

All 8 conflicting records carry both kinds of evidence, so the rule had to cover "both signals" too (decision: attachment unless the conversation has shared context). Every one has a file record whose uploader is the subject, and a message-side record with no order, agent or other-party context:

| Resource (case) | File signal | Message-side record | Before (name order) | After |
|---|---|---|---|---|
| support_9309 (119) | `attachment_pii_scan` file=delivery_confirmation_94.pdf | `support_ticket_read_completed`, no text | ATTACHMENT | ATTACHMENT |
| support_2315 (131) | `ticket_attachments` file_name=delivery_confirmation_84.pdf | `support_records` excerpt "Customer supplied contact details for a delivery query." | MESSAGE | ATTACHMENT |
| support_2283 (179) | `ticket_attachments` file_name | `support_records` excerpt "Customer contacted support about account access." | MESSAGE | ATTACHMENT |
| support_5830 (227), support_3684 (275), support_3615 (431), support_3896 (527) | `ticket_attachments` file_name | service export `message_excerpt`, generic | ATTACHMENT | ATTACHMENT |
| support_1370 (71) | `attachment_pii_scan` file=delivery_confirmation_12.pdf | `support_records` excerpt "Customer asked support to correct delivery details." | ATTACHMENT | ATTACHMENT |

Test diff: case_131 and case_179 replace REPORT_EXPOSED_PII / REDACT_RECORD / VERIFY_RECORD_REDACTED with REPORT_REMAINING_RECORD / DELETE_RECORD / VERIFY_RECORD_ABSENT. No verdict changes. The samples have no such conflict and are unchanged. Each decision is flagged `category_content:<resource>:<category>:<signal>`.

**Phantom-id search (read-only).** `audit_record_6645`, `audit_record_5569` and `account_004` appear in no participant file: text, SQL, JSON and log evidence, all 4 PDFs, the perception cache, and URL-encoded and escaped variants were all checked. They appear only in `sample_output.csv`. The near-misses are different ids (`account_6645`, `support_5569`, ticket `CS-5569`). There is no parser gap.

**Linking keys.** Nothing but identifiers and time links a request to an activity row. `system_activity.csv` carries no trace, ticket, job, session, ip or request id; log `trace_id` / `requestId` values only group lines inside one file.

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

## Remaining sample losses (iteration 19)

3 gold steps are still missed and there are no extra steps:

- 2 security audit records that appear in no participant data (`audit_record_6645`, `audit_record_5569`);
- `account_004` (case_04), which appears in no participant data either.

(Iteration 20 recovered the five contested accounts, iteration 21 the six identifier-less analytics rows.)

## Honest limits

What this solution does not do, or does not know, measured where possible.

- **52 of 507 test deletion cases have no account step** (144 before iteration 20; 31 of the 52 are INCOMPLETE, so invariant 3 reports 31 violations on test, 98 before). Gold has an account step in 32 of 32 sample cases. 8 have no status-matched row in the band, 6 have exactly one that the phase test, ownership or another request removes, and 38 have two or more that the run clock and the matching rule do not separate. A nearest-to-`reported_at` tie-breaker was measured (iteration 18): only 2 of 6 correct on the real contested sample cases, so it stays out, and the solution abstains.
- **3 sample gold resources have no participant evidence.** `audit_record_6645`, `audit_record_5569` and `account_004` appear in no participant file under any encoding (iteration 19 search). No evidence-grounded pipeline can produce them, so sample recall tops out below 1.000.
- **Analytics rows are linked by timing, not identifiers.** After anonymization they carry no identifier, so a pick rests on the run clock (iteration 21) plus ownership and exclusivity. It was 64 correct, 0 wrong on the identifier-linked analytics hidden for validation and added 0 steps on the 19 sample cases without analytics, but test cases without an analytics record cannot be checked. Where the data allows several rows, the solution abstains.
- **The run clock is a timing regularity of the deletion worker, learned from the data.** Band, windows and residues are measured on the 238 evaluation requests whose account is linked by identifiers, and used only to link activity rows, never to decide a state. If the hidden test set's worker runs followed a different clock the filters would reject true rows and the solution would abstain, not guess (held-out check: 0 rejected of 238; hide-and-force 235 correct, 0 wrong). It is not independent evidence about a person's data, and the report says so rather than presenting it as such.
- **Unmeasured test-only behavior.** The samples never exercise ownership conflicts (87 test picks refused, 0 on samples), support message/attachment conflicts (8 test records, decided by content in iteration 19), or the 116 test security-audit records whose only evidence was captured after `audit_at`. These rules follow the specification, but no gold confirms them.
- **The skeptic is not a reliable error detector.** In the negative control it caught 0 of 11 planted wrong picks (iteration 13). It is kept as a gated, no-change guard on the samples and skipped on test (budget 0); the check it could not make became deterministic code (iteration 14).
- **Prompt text is frozen.** The cache keys include the prompt and tool text, so known wording issues (for example, the skeptic prompt says "at most six" tool calls while the code allows 8 turns) are documented in `prompts.md` contracts instead of being edited.
- **Free-tier model use.** Every model call went to free tiers: Google Gemini (gemini-3.6-flash for perception, gemini-3.5-flash-lite for intake, category and skeptic) and Groq as a fallback. Free-tier terms can allow the provider to keep or use submitted inputs. What was sent: request texts and the 11 screenshots as they are (the dataset is fictional by the challenge rules), category examples with emails, phones, user ids and hashes masked, and skeptic tool output with identifiers replaced by owner labels. No key or credential is in any prompt, log or cache entry. Daily quotas shaped several choices (perception model, skeptic budget).
- **Failure handlers are tested with fake clients** (the HTTP 400 `reasoning_effort` retry and the blast-radius stop, iteration 24); the robustness table in `code/ARCHITECTURE.md` names a test for every row. The skeptic's blast-radius stop is tested at the predicate (`exceeds_blast_limit`), not through a full `main.py` run.

## Metrics

Produced by `python code/evaluation/main.py --write` after `python code/main.py --mode deterministic --split all`.
Steps are matched as a multiset on (phase, system_id, resource_id, operation, state) for precision and recall.
Field accuracy is given two ways. **Positional** compares step i of the prediction with step i of gold over the
longer list, so one missing step shifts and penalizes every field after it; it measures how close the serialized
sequence is. **Step-aligned** first pairs steps by (phase, resource_id), preferring the same operation, and compares
fields only over those pairs; it measures whether a step that was found is right in each field, independent of
missing or extra steps (phase and resource_id are 1.000 by construction). Edit distance is Levenshtein over whole
steps (all six fields), divided by the longer length.

| Metric | Samples (32) | Custom overlays (8) |
|---|---|---|
| Cases | 32 | 8 |
| Status accuracy | 32/32 (1.000) | 8/8 (1.000) |
| Steps (gold / predicted / matched) | 193 / 190 / 190 | 34 / 34 / 34 |
| Step precision / recall / F1 | 1.000 / 0.984 / 0.992 | 1.000 / 1.000 / 1.000 |
| Field accuracy, positional: phase | 0.984 | 1.000 |
| Field accuracy, positional: system_id | 0.959 | 1.000 |
| Field accuracy, positional: resource_id | 0.959 | 1.000 |
| Field accuracy, positional: operation | 0.959 | 1.000 |
| Field accuracy, positional: state | 0.959 | 1.000 |
| Field accuracy, positional: evidence_id | 0.979 | 1.000 |
| Step-aligned pairs | 190 | 34 |
| Field accuracy, step-aligned: phase | 1.000 | 1.000 |
| Field accuracy, step-aligned: system_id | 1.000 | 1.000 |
| Field accuracy, step-aligned: resource_id | 1.000 | 1.000 |
| Field accuracy, step-aligned: operation | 1.000 | 1.000 |
| Field accuracy, step-aligned: state | 1.000 | 1.000 |
| Field accuracy, step-aligned: evidence_id | 1.000 | 1.000 |
| Sequence accuracy (exact ordered steps) | 29/32 (0.906) | 8/8 (1.000) |
| Mean normalized edit distance | 0.022 | 0.000 |

### Per-case diff: samples

```text
case_04: gold COMPLETE / pred COMPLETE, matched 1/2, extra 0
    - VERI VERIFY_RECORD_ABSENT       account_service/account_004 ABSENT ev=evidence_54
case_18: gold INCOMPLETE / pred INCOMPLETE, matched 13/14, extra 0
    - VERI VERIFY_RECORD_PRESENT      security_service/audit_record_6645 PRESENT ev=-
case_20: gold INCOMPLETE / pred INCOMPLETE, matched 7/8, extra 0
    - VERI VERIFY_RECORD_PRESENT      security_service/audit_record_5569 PRESENT ev=-
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
| 7 activity pick owned by another user | case_10 | new ledger evidence_9003 (2026-09-01, inside the request window): account_010 FOUND for mateo.rios@outlook.com (user_02) | An activity_temporal pick is refused when a record of that resource names another user. | A deletion_worker row near reported_at is a timing claim, not ownership: taking it would report and delete someone else's account (iteration 14 refused 87 such test picks). | INCOMPLETE, 3 steps; never account_010, account_4990 | pass (INCOMPLETE) |

Every mutation was confirmed to reach the fact store, so no pass is vacuous:
- case 2's post-audit ABSENCE is ingested and then excluded by `as_of(audit_at)`;
- case 3 leaves only the replica row of `search_1007`;
- case 4's `search_9999` exists with an email that resolves to no user;
- case 5b's uppercased key normalizes to `user_10`.
