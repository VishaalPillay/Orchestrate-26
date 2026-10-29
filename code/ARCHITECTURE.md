# PCDA architecture

**Core principle: the model reads, the code decides.** LLM output never becomes a status, a step or a citation directly. It becomes a candidate observation, which code grounds into a `Fact` or rejects.

Deletion completeness is treated as a proof problem:

1. Policy compiles to obligations.
2. Evidence facts discharge them.
3. Open obligations become findings plus remediation.
4. Each case produces a `Certificate`, and `steps_json` is projected from it.

## Layers

```
            dataset/ (participant files only)
                         |
   +---------------------+------------------------------+
   | INGEST   ingest/registry -> {json,log,sql,txt,pdf,image}_adapter
   |          pdf: pymupdf text first; png: perception agent (cached)
   +---------------------+------------------------------+
                         |  list[Fact]
   +---------------------v------------------------------+
   | FACTS    facts.FactStore  (by identifier, by resource, as_of(t))
   +-----+--------------------+-------------------+-----+
         |                    |                   |
   +-----v------+   +---------v--------+  +-------v--------+
   | IDENTITY   |   | LINEAGE          |  | POLICY         |
   | closure +  |   | claims from      |  | rules ->       |
   | per-case   |   | system_activity  |  | templates      |
   | attribution|   | (never state)    |  | (+ DATA_FLOW)  |
   +-----+------+   +---------+--------+  +-------+--------+
         |                    |                   |
   +-----v--------------------v-------------------v-----+
   | SOLVER   obligations discharged by facts -> Certificate
   +---------------------+------------------------------+
                         |            ^ advisory objections only
                         |      agents/skeptic
   +---------------------v------------------------------+
   | PLANNER  Certificate -> steps (canonical key, citation rule)
   +---------------------+------------------------------+
                         |
   +---------------------v------------------------------+
   | VALIDATOR contract checks + lineage safety; raises, never writes
   +---------------------+------------------------------+
                         |
                     output.csv

   agents/intake, agents/perception, category fallback --> llm/client
   llm/client: LLM_PROVIDERS chain (gemini, groq) over one OpenAI-compatible SDK; disk cache + token ledger
```

## Per-case control flow (`pipeline.run_case`)

Each case runs these stages in order. The global context (rules, identity closure, lineage, FactStore) is built once.

`intake → subject → attribution (full store) + joint activity links → skeptic adjustments → canonical system_id → intake corroboration → store.as_of(audit_at) → solve (re-solved once with fallback-only links if nothing is provable) → skeptic → project → order`

Attribution reads the full store, and everything after it reads `as_of(audit_at)` (see the ownership/state split below). The joint activity passes and the canonical system map are computed once over all 550 requests in `build_context`, so a case's answer never depends on `--split`.

## Modules

### `pcda` package

| Module | Role |
|---|---|
| `paths.py` | Repo-relative paths for the dataset, the output and the cache. Never depends on the working directory. |
| `config.py` | Non-secret run configuration. `skeptic_budgets()`: model calls per split (sample 60, test 0), overridden by `SKEPTIC_BUDGET_<SPLIT>` and then `--skeptic-budget SPLIT=N`. A split with budget 0 is skipped and its cases are flagged `skeptic_skipped:budget`. |
| `contract.py` | The output contract as data: columns, step fields, phases, the operation table and `canonical_sort_key`. A leaf module. |
| `cases.py` | `CaseRequest` loader, the split definitions, and the evidence-id clock (manifest, activity and audit times). |
| `facts.py` | `FactKind`, `Fact` (frozen, equality and hash on `fact_id`) and `FactStore`. `as_of(t)` requires `captured_at <= t` and `observed_at <= t`. |
| `category.py` | `CategoryResolver`: a static `(name, signal)` map, plus an LLM fallback limited to the 15 categories and cached per name. The LLM fallback (`CATEGORY_MODEL`, model modes only) runs only when no static fact name decides a resource, and before the activity-target fallback. It sends the name, the attribute keys, up to 3 example records with identifiers masked, and the 15 categories with their rule text. The answer must be on the list or UNKNOWN, and it is cached per name. `PCDA_DISABLE_INFERRED=1` hides the inferred list, for experiments. `resolve_resource` pools every fact's names in precedence order: record_type/category, then collection, then table/event/title, then the activity target system when no fact name decides. Unknown names are flagged, never guessed. `INFERRED_FROM_EVIDENCE` lists the names whose category was read from evidence plus gold (case_12 opt-in → MARKETING_PROFILE, case_10 contact index → SECONDARY_IDENTIFIER), kept apart so a later run can test whether the LLM fallback recovers them. |
| `identity.py` | Normalizers, `IdentityClosure` (a global union-find over identifiers, exact matches only; an identifier claimed by two users is never unioned) and `ResourceAttribution` (per case, by tier, with a staleness gate, a post-audit guardrail and a gated `activity_temporal`), and `JointActivityAttribution`: the lowest tier, `activity_joint`, for resources linked only by deletion_worker rows, solved as one forced assignment over all 550 requests by `propagate` (a row serves one request, a resource one subject, account results match `reported_status`, exclusivity and identifier ownership are constraints inside the loop, activity_temporal picks are held as forced singletons; simultaneous rounds, so order-independent; two subjects forced to one resource are both refused; a `two_sided` mode also requires the row to be forced to the request). The staleness gate spares candidates matched on the subject's own `user_id`, which is never reassigned. **Ownership exclusivity** (`conflicting_owner`): an `activity_temporal` pick is refused when an ownership-eligible fact (a record of the resource, or one that names it as `source_resource_id`; never coverage or an absence) resolves only to other users under any of their identifiers. The refused pick comes back among the rejected resources with `conflict_fact` and `conflict_user`. |
| `lineage.py` | `Claim` and `LineageGraph`: active flows at t (claims only), reachability from `RESTORE_BACKUP` details, `affects()` for the validator, `non_deletion_classifications()` for intake corroboration, and `canonical_systems()`. That last one learns a resource-id prefix → system_id map by tier (data-catalog rows, then deletion_worker policy actions, then any row). Raw row-majority would mis-map `audit_record_`, `export_` and `support_`. |
| `policy.py` | `ObligationTemplate` and `compile_rules` (one template per category; fails if any of the 15 is missing). BACKUP_ARCHIVE is RETAIN with required state BLOCKED, SECURITY_AUDIT_LOG and LEGAL_HOLD_RECORD are PRESENT, ACCOUNT_TOMBSTONE is REDACT (user_id is a reference, never PII), and an implicit DATA_FLOW → DISABLED template is added. |
| `solver.py` | `Obligation`, `ObligationStatus`, `Certificate`, `Verdict`, `resource_state()` and `solve()`; see "Solver semantics" below. |
| `planner.py` | `Step`, `order_steps`, `serialize_steps`, `cite()` and `project()`. An OPEN obligation gives a finding (cited), a remediation and a verification (both `""`). INCOMPLETE verification covers the remediated resources, the account (USER_PROFILE) and every PRESENT-retained record. COMPLETE verifies every discharged obligation with its proving evidence. Citation: the latest fact naming the resource at or before `audit_at`; for an unmentioned absence, the latest non-vacuous covering snapshot; never a late evidence id. |
| `validator.py` | Contract checks and the lineage-safety check. Raises `ContractViolation` listing every failure. |
| `pipeline.py` | `build_context()` and `run_case()`: explicit stage calls with no framework. `JOINT_PASSES` declares the activity-joint passes: accounts always, analytics only for the NOT_APPLICABLE fallback. `canonicalize()` applies the canonical system and merges duplicate attributions. Deterministic mode uses `RuleIntakeAgent` alone (no LLM intake). The per-case `ctx.skeptic.challenge` hook is the advisory `NullSkepticAgent` in every mode; the LLM skeptic acts only through `skeptic_adjustments`. `find_shared_picks` runs once over all 550 requests: an activity pick (temporal or joint) that would go to two different subjects is refused for all of them. Every refusal is flagged `activity_pick_refused:<resource>:<fact or activity id>:<user or shared_by_cases>`. With the skeptic off for a split, its cases get `skeptic_skipped:budget`. |

### `ingest/`

| Module | Role |
|---|---|
| `base.py` | `ManifestRow`, the `Adapter` protocol, `IngestReport` and `SkippedEvidence`. |
| `registry.py` | Dispatch by extension, and `ingest_all()` into one global `FactStore`. |
| `common.py` | Shared by all adapters: `Emitter` (deterministic fact ids, manifest `captured_at`), identifier normalization, `view_record` (identifiers and which direct-identifier fields are still populated), ledger vocabulary mapping. |
| `json_adapter.py` | Ledgers, snapshot bundles (coverage from empty collections), service resource exports and flat collections (`{captured_at, source, collection, records}`). |
| `log_adapter.py` | Six log styles, grouping by `trace_id` with a per-line fallback. A 404 lookup is ABSENCE. |
| `sql_adapter.py` | Loads into in-memory sqlite. Topology comes from the header or `snapshot_scope`. Emits coverage facts with an `is_vacuous` flag. |
| `txt_adapter.py` | Pipe-delimited handover records. |
| `pdf_adapter.py` | The pymupdf text layer is primary. Perception is used only when the text is unusable (not wired up yet: such a PDF is skipped and recorded). |
| `image_adapter.py` | PNG via perception, cache first in every mode. Grounding is in code: a resource_id must match a known prefix (system_activity and text evidence); when the model leaves it empty or wrong but transcribed exactly one known-pattern id for the record, that id is adopted and logged, identifiers are normalized, observed_at is the latest visible timestamp at or before captured_at (else captured_at), state words map through fixed vocabularies, and facts get `src=perception` (LEDGER tier, below db_row). Images are ingested after all text files, so `cross_check` flags identifiers that disagree with text facts for the same resource. Without a cached answer in deterministic mode the file is skipped and the case is flagged. |

### `agents/`

| Module | Role |
|---|---|
| `intake.py` | `IntakeAgent`: does `request_text` authorize deletion? Its answer is an input to the solver. `RuleIntakeAgent` is the deterministic baseline: explicit guards ("don't delete", "keep the account", "not asking for deletion", "marketing … only"), then non-deletion request kinds, then multilingual deletion verbs; anything else is AMBIGUOUS, defaults to deletion and is flagged. `LLMIntakeAgent` (model modes, `INTAKE_MODEL`) classifies all 550 requests in fixed batches of 20, sorted by case_id, so cache keys don't depend on the split. Request text is fenced in `<request id=…>` blocks and declared to be data. An answer survives only if its `evidence_span` is verbatim in that request and its fields are in the enum. `decide_intake` combines rules, LLM and corroboration. No usable LLM answer means the rules decide. When the two agree, the case is done. When they disagree, it is flagged `intake_conflict:llm`, and a CLASSIFY_REQUEST non-deletion row on the subject's resources decides. Otherwise an LLM non-deletion answer wins only if its span is a negation (safety). A rule guard is never overridden by an LLM deletion answer. Deterministic mode uses the rules alone. |
| `perception.py` | `PerceptionAgent`: one call per image, prompt in `agents/prompts.md` (loaded by `agents/prompts.py`), returns an untrusted `PerceivedScreen` of transcribed records. It never decides a category, a state or an owner. |
| `skeptic.py` | The one agentic loop. `LLMSkepticAgent.investigate(target)` runs a tool-calling model for at most 8 calls per target (`submit_verdict` ends it) and returns CONFIRM, CHALLENGE (kind OTHER_SUBJECT or OTHER_REQUEST) or PROPOSE (MISSED_LINK). When the gate rejects a CHALLENGE or PROPOSE, the rejection (failed check, passed checks, cited id, retries left) goes back to the agent as the `submit_verdict` tool result, at most `MAX_GATE_RETRIES` = 2 times; after that the claim changes nothing. A CONFIRM is never sent back, so recorded CONFIRM conversations replay unchanged. `run_queue` walks the risk-ordered queue under a hard global call budget (`config.skeptic_budgets()`: sample 60, test 0; a cache hit costs a call, so replay walks the same queue), discards an unfinished target, and stops after 3 consecutive provider failures. It never writes a step, status or citation. `NullSkepticAgent` is the per-case advisory hook in every mode; the LLM skeptic never runs in deterministic mode. |
| `skeptic_tools.py` | `SkepticWorld` (full store, lineage, closure, templates, first-pass results) and the six read-only tools `case_context`, `find_facts`, `resource_timeline`, `activity_for`, `candidate_resources`, `get_rule`, with their schemas. Output is canonical JSON capped at 1,500 characters; identifiers are shown as owners (`subject`, `user_N`), never as raw emails or hashes; every item carries `after_audit`. |
| `skeptic_gate.py` | The only code that turns an answer into a change. CHALLENGE/OTHER_SUBJECT removes an activity pick when the cited fact or catalog row links the resource to a user other than the subject. CHALLENGE/OTHER_REQUEST removes it when the cited deletion_worker row is closer to another request's `reported_at` (within `ACTIVITY_WINDOW`), fits that request's status, and that request holds no fact-based link. PROPOSE adds a resource at `LinkTier.SKEPTIC` (lowest) when the cited fact resolves only to the subject, mentions the resource, and passes the post-audit and staleness gates. A cited id found in neither store is counted as hallucinated. |
| `skeptic_queue.py` | Queue order: activity picks beyond the sample envelope or owned by another user per the facts, the fallback, single-fact COMPLETE verdicts, the remaining picks by timing gap, contested accounts, then advisory flags. |
| `skeptic_run.py` | `run_skeptic` (first pass without the skeptic, queue per split, adjustments stored on the context), the JSONL records, `blast_radius` and the audit table. `main.py` stops before writing `output.csv` when the skeptic would change more than 10 test verdicts or 25 test steps (`--accept-skeptic-blast-radius` overrides) and writes `skeptic_candidate_output.csv` instead. |

### `llm/`

| Module | Role |
|---|---|
| `client.py` | `RunMode`, `OpenAICompatProvider` and `LLMClient`. **Decision change:** this replaces the earlier SDK-per-provider design (google-genai, groq). Gemini and Groq both expose OpenAI-compatible endpoints, so one SDK (`openai`) serves both with a per-provider `base_url`. The chain comes from `LLM_PROVIDERS`; each request names its model env var (`INTAKE_MODEL`, `PERCEPTION_MODEL`, ...), and Groq uses `GROQ_FALLBACK_MODEL`. Per call: an RPM limiter (`LLM_RPM_LIMIT`), thinking off when `LLM_DISABLE_THINKING=1` (a model that rejects `reasoning_effort: "none"` with HTTP 400, such as gemini-3.5-flash-lite, is retried once at `"low"` and remembered), at most 3 attempts honoring Retry-After, a timeout from `LLM_TIMEOUT_S`, and strict JSON with one repair turn. Keys are never printed or logged. |
| `cache.py` | Content-hash `DiskCache` under `code/.cache/llm/`, keyed on the requested model, the prompt, the schema and the attachment bytes. It does not depend on which provider in the chain answered. |
| `ledger.py` | `TokenLedger`: calls, cache hits, errors and input/output tokens per provider and model. Every event is appended to `code/.cache/runs/run_log.jsonl` (no prompts, answers or keys). |

### Top level

| File | Role |
|---|---|
| `main.py` | CLI: `--mode {deterministic,live,replay} --split {sample,test,all} --workers N`. It prints the canonical system map, an intake summary and corroboration, and writes per-case flags to `code/.cache/runs/flags_<split>.json`. |
| `evaluation/score.py` | Scorer against `sample_output.csv`: status accuracy, step P/R/F1, evidence and field accuracy, exact sequences, and a per-case diff. |
| `tools/perceive_images.py` | Runs perception alone (`--mode live` once, then from the cache), printing raw transcriptions, grounded facts and drops, for manual review. |
| `tools/intake_compare.py` | Runs LLM intake for one `--model` on all 550 requests and reports agreement with the rules, disagreements with both rationales, discarded answers, calls and tokens. |
| `evaluation/custom_cases.py` | Eight dataset overlays (copy `dataset/`, one mutation, `main.py --dataset-dir … --case … --out …`) with literal expected verdicts and steps. |
| `tools/check_attribution.py` | Attribution acceptance against the samples, including the case_17 decoy check. It does not run the joint passes. |
| `tools/leak_audit.py` | Every flag and silent rejection (stale, post-audit, perception drops, UNKNOWN by category) grouped by type, test vs samples, with a recall/precision label. No gold. |
| `tools/invariants.py` | Output invariants: cited evidence mentions the resource, shared resources are identifier-linked, INCOMPLETE cases verify an account, audit records are retained, per-system finding shares. |
| `tools/joint_order_check.py` | Builds every joint pass in file order and under three shuffles and checks identical assignments. |
| `tools/status_result_check.py` | `reported_status` against account and analytics row results, identifier-attributed resources only. |
| `tools/output_diff.py` | Verdict flips, step changes and per-system resource changes between two output files. |

## Run modes

| Mode | Provider calls | Cache |
|---|---|---|
| `deterministic` | never | read only; image files with no cache entry are skipped and the case is flagged. Rule intake, static categories. |
| `live` | each provider in `LLM_PROVIDERS`, in order | read and write. LLM intake, category fallback and perception. This produced the committed `output.csv`. |
| `replay` | never | read only; a miss is an error. Reproduces the live `output.csv` byte for byte. |

Note: the cache key is the **requested** model, prompt, schema and image bytes. An answer from a fallback provider is stored under the requested model's key, so replay never depends on which provider answered. A model comparison must therefore restrict `LLM_PROVIDERS` to the provider being measured.

## Solver semantics

- **State at `audit_at`.** Resource facts (LINK and coverage excluded) observed last decide. Among facts tied at that instant the conservative reading wins: present > absent, exposed > cleared, ACTIVE > DISABLED/BLOCKED. So a row present in any replica is present. For REDACT, ANONYMIZE and tombstone rules, `pii_fields` decides exposed or cleared.
- **Table snapshots** prove absence only for a resource that no fact names. A newer snapshot never overrides an older record-level fact (iteration 9a: overriding broke sample cases 05 and 06).
- **Burden of proof for guards.** A flow or archive whose existence is evidenced at `audit_at`, but whose disabled or blocked state is not, is OPEN and cites the existence fact. COMPLETE needs proof that no sync or restore path can recreate the data.
- **UNKNOWN** (nothing provable either way) cannot yield a cited finding, so on its own it never makes a case INCOMPLETE. It is flagged and left out of a COMPLETE case's steps. A case with no provable obligation at all becomes the NOT_APPLICABLE fallback, flagged `fallback_no_provable_obligation`.
- **Verdict:** NOT_APPLICABLE from intake; otherwise INCOMPLETE iff some obligation is OPEN, else COMPLETE.

## Ownership/state split

Two questions look alike and must not share a time rule.

| | Ownership: does resource R belong to the subject? | State: what is true of R at `audit_at`? |
|---|---|---|
| Time rule | Time-invariant. Evidence from **any capture time** may establish it. | Strictly `as_of(audit_at)`: `captured_at <= t` and `observed_at <= t`. |
| Where | `ResourceAttribution.for_case` takes the **full** `FactStore`. | The solver, citations, verdicts and the planner take the `as_of` store. |

Why: a record keeps its owner, but sample gold contains 8 resources whose only identifier-bearing evidence was captured after `audit_at` (for example `audit_record_4902` in `evidence_06`, captured two days after case_09's audit). Gold lists them with an empty `evidence_id`.

Guardrails on ownership:

- **Post-audit guardrail.** A resource counts only if it has at least one fact **observed** at or before `audit_at` (whatever the capture time of its file) or one `system_activity` row at or before `audit_at`. Resources first seen after the audit were created after it and are never pulled in. A fact's time is its `observed_at`: a record observed before the audit in a snapshot delivered later did exist at the audit. Read strictly by capture time, 5 of those 8 resources would be rejected.
- **Staleness gate.** A candidate needs one supporting observation at or after `requested_at`. Snapshots that predate the request describe an earlier life of the identifier (the case_17 decoy `account_6523`, last seen 162 days before the request).
- **No citation leakage.** `AttributedResource.evidence_ids` holds only evidence visible at `audit_at`. Evidence captured later is listed in `late_evidence_ids` and may never be cited or used for state.

Rejected candidates are returned by `for_case_detailed` with the reason as their last reason.

## Invariants

- **Step order:** steps are ordered only by `(operation index, system_id, resource_id)`. Lineage proves that order is dependency-safe; it does not produce it.
- **Activity rows are claims.** They can create attributions only through the gated `activity_temporal` tier and the joint `activity_joint` passes. A pick is refused if facts tie the resource to another user or two subjects would get it (ownership exclusivity); the survivors go to the skeptic where it runs (the samples), and are flagged `skeptic_skipped:budget` where it does not (test). Resource ids are never inferred from naming patterns. System ids are canonicalized from the learned prefix map, never taken from replica or store names.
- **Validation before writing:** the output is validated before it is written, and a violation means nothing is written.
- **Secrets and models:** keys and model names come only from the environment or `.env`: `LLM_PROVIDERS`, `<NAME>_BASE_URL`, `<NAME>_API_KEY`, `INTAKE_MODEL`, `CATEGORY_MODEL`, `PERCEPTION_MODEL` (currently gemini-3.6-flash), `SKEPTIC_MODEL`, `GROQ_FALLBACK_MODEL`, `LLM_RPM_LIMIT`, `LLM_DISABLE_THINKING` and `LLM_TIMEOUT_S`. Nothing is hardcoded, and no key is printed or logged.

## Cache as a reproducibility artifact

`code/.cache/` holds the LLM and perception cache (`llm/`), plus run outputs and usage ledgers (`runs/`). It is git-ignored, but **the packaging step must include `code/.cache/llm/` in `code.zip`**. Without it, `--mode replay` can't reproduce the submitted `output.csv` offline, and deterministic mode loses the cached image facts.

## Robustness

Known limits (accounts no row forces, evidence-less gold resources, unmeasured test-only rules, free-tier model use) are listed in `code/evaluation/evaluation_report.md` under "Honest limits".

Every failure point, what the code does, and the test that pins it. The design rule: a model failure degrades to the deterministic answer, never to a guess; a data or contract failure stops the run instead of writing a partial `output.csv`.

| Failure | Where | Handling | Test |
|---|---|---|---|
| Provider error (5xx, connection, timeout) | `llm/client.py` | `ProviderError(retryable)`; up to `config.LLM_MAX_ATTEMPTS` = 3 attempts with exponential backoff, then the next provider in `LLM_PROVIDERS`; all failing raises `LLMUnavailable`. Callers degrade: intake -> rules decide; category -> no category (flagged); perception -> image skipped and flagged `perception_skipped`; skeptic -> outcome ERROR | `test_llm_perception.py::test_retry_honours_retry_after_and_caps_attempts`, `::test_one_json_repair_then_fallback_provider` |
| Every provider down during the skeptic | `skeptic.run_queue` | After 3 consecutive ERROR targets the queue stops; the rest count as unreached; nothing changes | `test_failure_handling.py::test_three_consecutive_provider_failures_stop_the_queue` |
| HTTP 429 / quota | `llm/client.py` | Retryable; `Retry-After` honored; requests paced by `LLM_RPM_LIMIT` (default `config.LLM_RPM_LIMIT_DEFAULT`) | `test_llm_perception.py::test_retry_honours_retry_after_and_caps_attempts` |
| HTTP 400 for `reasoning_effort: "none"` (gemini-3.5-flash-lite) | `llm/client.py` | Retried once at `"low"` and remembered for that model | none (observed live in iteration 11a) |
| Malformed JSON | `llm/client.py`, every agent | One repair turn, then the next provider. Answers are validated in code: intake (enum, verbatim span), category (allowed list), perception (grounding), skeptic (`_parse_answer`, resubmit) | `::test_one_json_repair_then_fallback_provider`, `test_llm_perception.py::test_parse_screen_is_defensive`, `test_llm_intake_category.py::test_batches_fence_text_and_validate_spans`, `::test_category_fallback_rejects_off_list_answers`, `test_skeptic.py::test_submit_verdict_tool_ends_the_investigation_and_a_bad_one_is_resubmitted` |
| Replay cache miss | `llm/client.py` (`CacheMiss`) | Intake and category: propagate and abort the run (replay must be byte-identical, so a miss is an error, never a silent fallback). Perception: image skipped and flagged. Skeptic: ERROR, then the 3-failure stop | `test_llm_perception.py::test_modes_without_cache`, `test_skeptic.py::test_replay_of_an_unrecorded_conversation_is_a_cache_miss` |
| Unparseable evidence | `ingest/` | Text formats (JSON, SQL, log, txt) raise `ValueError` and stop ingest, so a file is never half-read silently. A PDF with no usable text layer and an image with no transcription are skipped and recorded in `IngestReport.skipped`. A perception record that fails grounding is dropped with a warning | `test_failure_handling.py::test_unparseable_evidence_fails_loudly`, `test_ingest_formats.py::test_image_is_skipped_and_flagged_in_deterministic_mode`, `test_llm_perception.py::test_grounding_drops_unknown_ids_and_normalizes_identifiers` |
| Unknown category | `category.py`, `solver.py` | Static map, then the LLM fallback (model modes), then the activity target system. Still unknown: flagged `category_unknown` and no obligation, so no step is invented. Message vs attachment ambiguity is decided from record content (`support_by_content`) | `test_policy_solver.py::test_absence_cites_latest_non_vacuous_covering_snapshot` (asserts `category_unknown`), `test_llm_intake_category.py::test_support_message_vs_attachment_is_decided_by_content_not_name_order` |
| Nothing provable for a case | `solver.solve` | NOT_APPLICABLE fallback, flagged `fallback_no_provable_obligation` (case_61) | `test_policy_solver.py::test_absence_cites_latest_non_vacuous_covering_snapshot` |
| Validator failure | `validator.validate_output`, `main.py` | `ContractViolation` lists every violation; `main` prints them, writes nothing and exits 1 | `test_failure_handling.py::test_validator_accepts_a_valid_output_and_lists_every_violation_otherwise`, `test_validator_lineage.py` |
| Skeptic budget exhausted | `skeptic.Budget`, `run_queue` | Hard global cap per split (`config.skeptic_budgets()`); a target cut mid-investigation is discarded; a split with budget 0 is skipped and its picks flagged `skeptic_skipped:budget` | `test_skeptic.py::test_budget_stops_mid_target_and_discards_it`, `::test_skeptic_budgets_come_from_config_env_then_overrides` |
| Skeptic claim rejected by the gate | `skeptic.investigate` | Structured rejection returned at most `MAX_GATE_RETRIES` = 2 times, then no change | `test_skeptic.py::test_a_gate_rejection_is_returned_to_the_agent_which_may_revise`, `::test_after_two_rejected_retries_the_claim_falls_back_to_no_change` |
| Skeptic changes too much | `main.py` (`blast_radius`) | More than `config.BLAST_MAX_VERDICTS` verdicts or `BLAST_MAX_STEPS` steps: `output.csv` is not written; `skeptic_candidate_output.csv` is | none |
