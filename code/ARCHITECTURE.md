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

`intake → subject → attribution (full store) → store.as_of(audit_at) → solve → skeptic → project → order`

Attribution reads the full store and everything after it reads `as_of(audit_at)`; see the ownership/state split below.

## Modules

### `pcda` package

| Module | Role |
|---|---|
| `paths.py` | Repo-relative paths for the dataset, the output and the cache. Never depends on the working directory. |
| `contract.py` | The output contract as data: columns, step fields, phases, the operation table and `canonical_sort_key`. A leaf module. |
| `cases.py` | `CaseRequest` loader, the split definitions, and the evidence-id clock (manifest, activity and audit times). |
| `facts.py` | `FactKind`, `Fact` (frozen, equality and hash on `fact_id`) and `FactStore`. `as_of(t)` requires `captured_at <= t` and `observed_at <= t`. |
| `category.py` | `CategoryResolver`: a static `(name, signal)` map, plus an LLM fallback limited to the 15 categories and cached per name. |
| `identity.py` | Normalizers, `IdentityClosure` (a global union-find over identifiers, exact matches only; an identifier claimed by two users is never unioned) and `ResourceAttribution` (per case, by tier, with a staleness gate, a post-audit guardrail and a gated `activity_temporal`), and `JointActivityAttribution`: the lowest tier, `activity_joint`, for accounts linked only by a deletion_worker DELETE_RECORD row (status/result consistency, elimination of other users' accounts, one row per request across all 550 requests; contested accounts are refused). |
| `lineage.py` | `Claim` and `LineageGraph`: active flows at t, reachability, and `affects()` for the validator. |
| `policy.py` | `ObligationTemplate` and `compile_rules`. BACKUP_ARCHIVE maps to BLOCKED, and there is an implicit DATA_FLOW → DISABLED template. |
| `solver.py` | `Obligation`, `ObligationStatus`, `Certificate`, `Verdict`, and `solve()`. |
| `planner.py` | `Step`, `order_steps`, `serialize_steps`, `cite()` and `project()`. |
| `validator.py` | Contract checks and the lineage-safety check. Raises `ContractViolation` listing every failure. |
| `pipeline.py` | `build_context()` and `run_case()`: explicit stage calls with no framework. |

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
| `image_adapter.py` | PNG via perception, cache first in every mode. Grounding is in code: a resource_id must match a known prefix (system_activity and text evidence), identifiers are normalized, observed_at is the latest visible timestamp at or before captured_at (else captured_at), state words map through fixed vocabularies, and facts get `src=perception` (LEDGER tier, below db_row). Images are ingested after all text files, so `cross_check` flags identifiers that disagree with text facts for the same resource. Without a cached answer in deterministic mode the file is skipped and the case is flagged. |

### `agents/`

| Module | Role |
|---|---|
| `intake.py` | `IntakeAgent`: does `request_text` authorize deletion? Its answer is an input to the solver. |
| `perception.py` | `PerceptionAgent`: one call per image, prompt in `agents/prompts.md` (loaded by `agents/prompts.py`), returns an untrusted `PerceivedScreen` of transcribed records. It never decides a category, a state or an owner. |
| `skeptic.py` | `SkepticAgent`: advisory objections, including every `activity_temporal` attribution. |

### `llm/`

| Module | Role |
|---|---|
| `client.py` | `RunMode`, `OpenAICompatProvider` and `LLMClient`. **Decision change:** this replaces the earlier SDK-per-provider design (google-genai, groq). Gemini and Groq both expose OpenAI-compatible endpoints, so one SDK (`openai`) serves both with a per-provider `base_url`. The chain comes from `LLM_PROVIDERS`; each request names its model env var (`INTAKE_MODEL`, `PERCEPTION_MODEL`, ...), and Groq uses `GROQ_FALLBACK_MODEL`. Per call: an RPM limiter (`LLM_RPM_LIMIT`), thinking off when `LLM_DISABLE_THINKING=1`, at most 3 attempts honoring Retry-After, and strict JSON with one repair turn. Keys are never printed or logged. |
| `cache.py` | Content-hash `DiskCache` under `code/.cache/llm/`, keyed on the requested model, the prompt, the schema and the attachment bytes. It does not depend on which provider in the chain answered. |
| `ledger.py` | `TokenLedger`: calls, cache hits, errors and input/output tokens per provider and model. Every event is appended to `code/.cache/runs/run_log.jsonl` (no prompts, answers or keys). |

### Top level

| File | Role |
|---|---|
| `main.py` | CLI: `--mode {deterministic,live,replay} --split {sample,test,all} --workers N`. |

## Run modes

| Mode | Provider calls | Cache |
|---|---|---|
| `deterministic` | never | read only; image files with no cache entry are skipped and the case is flagged |
| `live` | each provider in `LLM_PROVIDERS`, in order | read and write |
| `replay` | never | read only; a miss is an error |

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
- **Activity rows are claims.** They can create LINK attributions only through the four-condition `activity_temporal` gate, and every such attribution goes to the skeptic. Resource ids are never inferred from naming patterns.
- **Validation before writing:** the output is validated before it is written, and a violation means nothing is written.
- **Secrets:** keys come only from the environment or `.env` (`GEMINI_API_KEY`, `GROQ_API_KEY`, and optionally `GEMINI_MODEL` and `GROQ_MODEL`).

## Cache as a reproducibility artifact

`code/.cache/` holds the LLM and perception cache (`llm/`), plus run outputs and usage ledgers (`runs/`). It is git-ignored, but **the packaging step must include `code/.cache/llm/` in `code.zip`**. Without it, `--mode replay` can't reproduce the submitted `output.csv` offline, and deterministic mode loses the cached image facts.
