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

`intake → subject → attribution (full store) + joint activity links → canonical system_id → intake corroboration → store.as_of(audit_at) → solve (re-solved once with fallback-only links if nothing is provable) → skeptic → project → order`

Attribution reads the full store, and everything after it reads `as_of(audit_at)` (see the ownership/state split below). The joint activity passes and the canonical system map are computed once over all 550 requests in `build_context`, so a case's answer never depends on `--split`.

## Modules

### `pcda` package

| Module | Role |
|---|---|
| `paths.py` | Repo-relative paths for the dataset, the output and the cache. Never depends on the working directory. |
| `contract.py` | The output contract as data: columns, step fields, phases, the operation table and `canonical_sort_key`. A leaf module. |
| `cases.py` | `CaseRequest` loader, the split definitions, and the evidence-id clock (manifest, activity and audit times). |
| `facts.py` | `FactKind`, `Fact` (frozen, equality and hash on `fact_id`) and `FactStore`. `as_of(t)` requires `captured_at <= t` and `observed_at <= t`. |
| `category.py` | `CategoryResolver`: a static `(name, signal)` map, plus an LLM fallback limited to the 15 categories and cached per name. The fallback is not implemented; deterministic mode never calls it. `resolve_resource` pools every fact's names in precedence order: record_type/category, then collection, then table/event/title, then the activity target system when no fact name decides. Unknown names are flagged, never guessed. `INFERRED_FROM_EVIDENCE` lists the names whose category was read from evidence plus gold (case_12 opt-in → MARKETING_PROFILE, case_10 contact index → SECONDARY_IDENTIFIER), kept apart so a later run can test whether the LLM fallback recovers them. |
| `identity.py` | Normalizers, `IdentityClosure` (a global union-find over identifiers, exact matches only; an identifier claimed by two users is never unioned) and `ResourceAttribution` (per case, by tier, with a staleness gate, a post-audit guardrail and a gated `activity_temporal`), and `JointActivityAttribution`: the lowest tier, `activity_joint`, for accounts linked only by a deletion_worker DELETE_RECORD row (status/result consistency, elimination of other users' accounts, one row per request across all 550 requests; contested accounts are refused). |
| `lineage.py` | `Claim` and `LineageGraph`: active flows at t (claims only), reachability from `RESTORE_BACKUP` details, `affects()` for the validator, `non_deletion_classifications()` for intake corroboration, and `canonical_systems()`. That last one learns a resource-id prefix → system_id map by tier (data-catalog rows, then deletion_worker policy actions, then any row). Raw row-majority would mis-map `audit_record_`, `export_` and `support_`. |
| `policy.py` | `ObligationTemplate` and `compile_rules` (one template per category; fails if any of the 15 is missing). BACKUP_ARCHIVE is RETAIN with required state BLOCKED, SECURITY_AUDIT_LOG and LEGAL_HOLD_RECORD are PRESENT, ACCOUNT_TOMBSTONE is REDACT (user_id is a reference, never PII), and an implicit DATA_FLOW → DISABLED template is added. |
| `solver.py` | `Obligation`, `ObligationStatus`, `Certificate`, `Verdict`, `resource_state()` and `solve()`; see "Solver semantics" below. |
| `planner.py` | `Step`, `order_steps`, `serialize_steps`, `cite()` and `project()`. An OPEN obligation gives a finding (cited), a remediation and a verification (both `""`). INCOMPLETE verification covers the remediated resources, the account (USER_PROFILE) and every PRESENT-retained record. COMPLETE verifies every discharged obligation with its proving evidence. Citation: the latest fact naming the resource at or before `audit_at`; for an unmentioned absence, the latest non-vacuous covering snapshot; never a late evidence id. |
| `validator.py` | Contract checks and the lineage-safety check. Raises `ContractViolation` listing every failure. |
| `pipeline.py` | `build_context()` and `run_case()`: explicit stage calls with no framework. `JOINT_PASSES` declares the activity-joint passes: accounts always, analytics only for the NOT_APPLICABLE fallback. `canonicalize()` applies the canonical system and merges duplicate attributions. Deterministic mode uses `RuleIntakeAgent` and `NullSkepticAgent`. |

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
| `intake.py` | `IntakeAgent`: does `request_text` authorize deletion? Its answer is an input to the solver. `RuleIntakeAgent` is the deterministic baseline: explicit guards ("don't delete", "keep the account", "not asking for deletion", "marketing … only"), then non-deletion request kinds, then multilingual deletion verbs; anything else is AMBIGUOUS, defaults to deletion and is flagged. `LLMIntakeAgent` is not implemented yet, so live and replay runs stop at intake. A CLASSIFY_REQUEST "non-deletion" activity row on the subject's resources is recorded as corroboration and flagged `intake_conflict` when the text disagrees; the text still decides. |
| `perception.py` | `PerceptionAgent`: one call per image, prompt in `agents/prompts.md` (loaded by `agents/prompts.py`), returns an untrusted `PerceivedScreen` of transcribed records. It never decides a category, a state or an owner. |
| `skeptic.py` | `SkepticAgent`: advisory objections, including every `activity_temporal` and `activity_joint` attribution. `NullSkepticAgent` (deterministic mode) raises none; the pipeline flags those attributions `skeptic_pending`. `LLMSkepticAgent` is not implemented. |

### `llm/`

| Module | Role |
|---|---|
| `client.py` | `RunMode`, `OpenAICompatProvider` and `LLMClient`. **Decision change:** this replaces the earlier SDK-per-provider design (google-genai, groq). Gemini and Groq both expose OpenAI-compatible endpoints, so one SDK (`openai`) serves both with a per-provider `base_url`. The chain comes from `LLM_PROVIDERS`; each request names its model env var (`INTAKE_MODEL`, `PERCEPTION_MODEL`, ...), and Groq uses `GROQ_FALLBACK_MODEL`. Per call: an RPM limiter (`LLM_RPM_LIMIT`), thinking off when `LLM_DISABLE_THINKING=1`, at most 3 attempts honoring Retry-After, and strict JSON with one repair turn. Keys are never printed or logged. |
| `cache.py` | Content-hash `DiskCache` under `code/.cache/llm/`, keyed on the requested model, the prompt, the schema and the attachment bytes. It does not depend on which provider in the chain answered. |
| `ledger.py` | `TokenLedger`: calls, cache hits, errors and input/output tokens per provider and model. Every event is appended to `code/.cache/runs/run_log.jsonl` (no prompts, answers or keys). |

### Top level

| File | Role |
|---|---|
| `main.py` | CLI: `--mode {deterministic,live,replay} --split {sample,test,all} --workers N`. It prints the canonical system map, an intake summary and corroboration, and writes per-case flags to `code/.cache/runs/flags_<split>.json`. |
| `evaluation/score.py` | Scorer against `sample_output.csv`: status accuracy, step P/R/F1, evidence and field accuracy, exact sequences, and a per-case diff. |
| `tools/perceive_images.py` | Runs perception alone (`--mode live` once, then from the cache), printing raw transcriptions, grounded facts and drops, for manual review. |
| `tools/check_attribution.py` | Attribution acceptance against the samples, including the case_17 decoy check. It does not run the joint passes. |

## Run modes

| Mode | Provider calls | Cache |
|---|---|---|
| `deterministic` | never | read only; image files with no cache entry are skipped and the case is flagged. This is the mode that produces `output.csv` today. |
| `live` | each provider in `LLM_PROVIDERS`, in order | read and write |
| `replay` | never | read only; a miss is an error |

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
- **Activity rows are claims.** They can create attributions only through the gated `activity_temporal` tier and the joint `activity_joint` passes, and every such attribution goes to the skeptic. Resource ids are never inferred from naming patterns. System ids are canonicalized from the learned prefix map, never taken from replica or store names.
- **Validation before writing:** the output is validated before it is written, and a violation means nothing is written.
- **Secrets and models:** keys and model names come only from the environment or `.env`: `LLM_PROVIDERS`, `<NAME>_BASE_URL`, `<NAME>_API_KEY`, `INTAKE_MODEL`, `CATEGORY_MODEL`, `PERCEPTION_MODEL` (currently gemini-3.6-flash), `SKEPTIC_MODEL`, `GROQ_FALLBACK_MODEL`, `LLM_RPM_LIMIT`, `LLM_DISABLE_THINKING` and `LLM_TIMEOUT_S`. Nothing is hardcoded, and no key is printed or logged.

## Cache as a reproducibility artifact

`code/.cache/` holds the LLM and perception cache (`llm/`), plus run outputs and usage ledgers (`runs/`). It is git-ignored, but **the packaging step must include `code/.cache/llm/` in `code.zip`**. Without it, `--mode replay` can't reproduce the submitted `output.csv` offline, and deterministic mode loses the cached image facts.
