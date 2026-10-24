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
   llm/client: Gemini -> Groq -> cache only; disk cache + token ledger
```

## Per-case control flow (`pipeline.run_case`)

Each case runs these stages in order. The global context (rules, identity closure, lineage, FactStore) is built once.

`intake → subject → store.as_of(audit_at) → attribution → solve → skeptic → project → order`

## Modules

### `pcda` package

| Module | Role |
|---|---|
| `paths.py` | Repo-relative paths for the dataset, the output and the cache. Never depends on the working directory. |
| `contract.py` | The output contract as data: columns, step fields, phases, the operation table and `canonical_sort_key`. A leaf module. |
| `cases.py` | `CaseRequest` loader, the split definitions, and the evidence-id clock (manifest, activity and audit times). |
| `facts.py` | `FactKind`, `Fact` (frozen, equality and hash on `fact_id`) and `FactStore`. `as_of(t)` requires `captured_at <= t` and `observed_at <= t`. |
| `category.py` | `CategoryResolver`: a static `(name, signal)` map, plus an LLM fallback limited to the 15 categories and cached per name. |
| `identity.py` | Normalizers, `IdentityClosure` (a global union-find over identifiers) and `ResourceAttribution` (per case, by tier, with a gated `activity_temporal`). |
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
| `image_adapter.py` | PNG via perception. In deterministic mode without a cache, the file is skipped and the case is flagged. Perception is not implemented yet, so every PNG is skipped. |

### `agents/`

| Module | Role |
|---|---|
| `intake.py` | `IntakeAgent`: does `request_text` authorize deletion? Its answer is an input to the solver. |
| `perception.py` | `PerceptionAgent`: returns raw observations from images or unusable PDF text, grounded by code. |
| `skeptic.py` | `SkepticAgent`: advisory objections, including every `activity_temporal` attribution. |

### `llm/`

| Module | Role |
|---|---|
| `client.py` | `RunMode`, the `Provider` protocol, `GeminiProvider` and `GroqProvider` (official SDKs, lazy import), and `LLMClient` (retries honor Retry-After). |
| `cache.py` | Content-hash `DiskCache` under `code/.cache/llm/`. The key is independent of the provider. |
| `ledger.py` | `TokenLedger`: calls, cache hits and input/output tokens per provider and model. |

### Top level

| File | Role |
|---|---|
| `main.py` | CLI: `--mode {deterministic,live,replay} --split {sample,test,all} --workers N`. |

## Run modes

| Mode | Provider calls | Cache |
|---|---|---|
| `deterministic` | never | read only; image files with no cache entry are skipped and the case is flagged |
| `live` | Gemini, then Groq | read and write |
| `replay` | never | read only; a miss is an error |

## Invariants

- **Step order:** steps are ordered only by `(operation index, system_id, resource_id)`. Lineage proves that order is dependency-safe; it does not produce it.
- **Activity rows are claims.** They can create LINK attributions only through the four-condition `activity_temporal` gate, and every such attribution goes to the skeptic. Resource ids are never inferred from naming patterns.
- **Validation before writing:** the output is validated before it is written, and a violation means nothing is written.
- **Secrets:** keys come only from the environment or `.env` (`GEMINI_API_KEY`, `GROQ_API_KEY`, and optionally `GEMINI_MODEL` and `GROQ_MODEL`).

## Cache as a reproducibility artifact

`code/.cache/` holds the LLM and perception cache (`llm/`), plus run outputs and usage ledgers (`runs/`). It is git-ignored, but **the packaging step must include `code/.cache/llm/` in `code.zip`**. Without it, `--mode replay` can't reproduce the submitted `output.csv` offline, and deterministic mode loses the cached image facts.
