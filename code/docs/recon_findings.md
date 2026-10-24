# Recon findings: eight questions on the dataset

All scripts are in `code/tools/recon/` (`ev.py` normalizes evidence, `textidx.py` indexes text, the `qN*.py` files answer individual questions). Nothing else was built. I viewed all 11 PNGs by eye, since no OCR is available.

## 1. Ordering

- **The sample can't settle string vs numeric.** There are 163 same-`(phase, operation)` runs and none violate string order or numeric order.
- **Same-system pairs never occur.** Across 193 `(case, phase, operation, system_id)` groups, none has two resources. Digit-width differences like `search_052` vs `search_1308` therefore never appear in one group, and no counterexample exists.
- **What the data does show:** all 28 multi-step groups span different systems and fit sorting by `system_id`. They fit equally well sorting by `resource_id` alone.
- **Phases:** they are always `FINDING`, then `REMEDIATION`, then `VERIFICATION`. Each `(phase, operation)` is one contiguous run, with no conflicting pairs.
- **Operation order seen:** remediation runs `DISABLE_DATA_FLOW`, `DELETE_RECORD`, `REDACT_RECORD`, `ANONYMIZE_RECORD`. Verification runs ABSENT, PRESENT, REDACTED, ANONYMIZED, DATA_FLOW_DISABLED, RESTORE_BLOCKED. `BLOCK_RESTORE` and `DISABLE_DATA_FLOW` never co-occur in a sample case.
- **Digit widths in the data:** 3-digit and 4-digit IDs both exist, for example 69 of 690 search IDs in `system_activity.csv`.

## 2. Hashing

- **All 850 rows match** `sha256(email.strip().lower())`, with 0 mismatches. All emails are already lowercase and trimmed, with no duplicate emails or user IDs.
- **Historical hashes match too:** all 214 old and new values in `email_hash` audit rows equal the sha256 of the paired email value, with 0 mismatches.
- **Audit-log fields:** `user_audit_log.csv` has only `email` (107), `email_hash` (107) and `phone` (92) changes, and every email change has a paired hash change.

## 3. Evidence formats

Counts: 29 `records` (28 JSON + 1 txt), 26 logs, 24 SQL, 11 PNG, 4 PDF.

| Format | system_id / resource_id | Category | Identifiers | Timestamp |
|---|---|---|---|---|
| **json** (ledger style: connector_configurations, export_manifest, security_audit_ledger) | `system`, `resource_id` per record | `record_type` (`USER_PROFILE`, `DATA_FLOW`…) | `subject_reference` (email or `user_N`) | `recorded_at` per record; `exported_at` for the file |
| **json** (bundles) | `data_store`, `records[].resource_id` | `collection` (`customer_profiles`, `deleted_customer_references`…) | `email`, `phone`, `email_hash`, `customer_reference`, or null | `snapshots[].captured_at` |
| **json** (service_resource_export) | `service_exports.<service>` | collection key (`profile_lookups`, `restore_entries`…) | `profile.email`, `customer_email`, `subject_reference` | `observed_at` per record |
| **log** | `service=` or logger, `resource_id=` | not stated; implied by `event=` | `lookup_key`, `match_key`, `correlation_key`, `requester_email`… | line prefix timestamp |
| **txt** | pipe-delimited columns | `record_type` column | last column | first column |
| **sql** | `origin_system` and `resource_id` columns, or the table itself | `collection_name` or table | inside `record_json`, or columns | `updated_at` / `indexed_at`, plus the `-- Captured at:` header |
| **pdf** | `resource_id` appears in text (`support_001`) | implied by content | email, phone, `user_N` | "captured 12 Sep 2026 07 30 UTC" (layout-scrambled) |
| **png** | not machine-readable; IDs are visible on screen (e.g. `account_5762`, `search_428`) | implied by the app | visible email, phone | on-screen date |

Real examples, one per format:

- **json (bundle):** `{"resource_id":"account_5334","customer_reference":"user_24","email":"keiko.nakamura@gmail.com"}` in `evidence_51`'s family (`evidence_02`).
- **log:** `...resource_id=analytics_9914 event=contact_lookup_completed rows=1 email_opt_in=true correlation_key="44a65154…"` (`evidence_01`).
- **txt:** `2026-10-01T12:00:00Z | integration_service | data_flow_9522 | DATA_FLOW | ACTIVE | novak.tomasz@icloud.com` (`evidence_03`).
- **sql:** `customer_data_primary('analytics_1738','analytics_platform','customer_analytics_reports','{…"user_id":"user_52"…}','2026-09-21T10:00:00Z')`.
- **pdf:** `evidence_58` reads "Support reference CS-4827 / support_001", `priya.raman@gmail.com`, `+91 98765 73142`.
- **png:** `search_index_inspector_20260901.png` shows `search_428 · g.silva76@gmail.com · user_13`.

**Log formats:** there are at least six styles (key=value, JSON with a timestamp prefix, syslog, bracketed trace, pipe-delimited, and a cloud export with the payload inside `message="…"`). In the service-events logs, the identifier is on a `profile_lookup_started` line (`lookup_key=`) and the `resource_id` is on a later `…_completed` line. They are joined only by `trace_id`. Example: case_17's `account_4092` is linked to the user's email only through `tr-4708586`.

**SQL tables and topologies (24 files):**

- **`read_replica` (5):** `customer_data_primary` + `customer_data_read_replica`.
- **`regional_replicas` (3):** `customer_data_primary` + `customer_data_replica_ap_south` + `customer_data_replica_eu_west`.
- **`reporting_replica` (2):** `customer_data_primary` + `customer_data_reporting_replica`.
- **`failover_replica` (2):** `evidence_52` has `customer_data_primary` + `customer_data_failover_replica`. `evidence_54` has `customer_profiles_primary` + `customer_profiles_failover_replica`.
- **`primary_only` (1):** `evidence_57`, with `search_documents_primary`.
- **No topology header (11):** the `customer_data_inventory_*` files hold `snapshot_scope` plus 8 typed tables.
- **Drift examples:** `evidence_05` has `account_994` only in the replica (case_16's gold resource), and `evidence_54` has `account_005` only in the failover (case_05's).

**Time traps inside files:**

- **Records later than their file's `captured_at`:** `evidence_13` has `account_1417` NOT_FOUND at 08-12 and FOUND at 08-14, while the file is captured 08-12. `evidence_36` has `account_5043` NOT_FOUND at 04-09 and FOUND at 04-11.
- **Cases affected:** case_135 and case_383, whose `audit_at` equals the earlier timestamp, so the later FOUND must be ignored.
- **Other timestamp offsets:** otherwise, observation timestamps sit at or before `captured_at`. Log lines span hours to days before the capture time.

## 4. Flow and restore state

**`system_activity.csv` carries no disabled or blocked state.** The details text is one template per event type:

| Event | Count | Details text |
|---|---|---|
| `CHECK_DATA_FLOW` | 89 | "…confirmed that data flow X remains enabled." (all 89) |
| `CHECK_RESTORE_ACCESS` | 28 | "…archive X can still be restored." (all 28) |
| `DISABLE_DATA_FLOW` | 36 | "Policy action completed for X." |
| `BLOCK_RESTORE` | 36 | "Policy action completed for X." |

- **Failed and partial results:** a few `DELETE_RECORD` and `SYNC_RECORD` rows are `FAILED` or `PARTIAL`.
- **Claim vs evidence:** for the activity rows with evidence, 87 of 89 `CHECK_DATA_FLOW` and 24 of 28 `CHECK_RESTORE_ACCESS` have a later observation of enabled/true. 35 of 36 `DISABLE_DATA_FLOW` and 34 of 36 `BLOCK_RESTORE` have a later observation of disabled/false. This is the earliest observation after the activity, so it shows nothing contradicting the claims, not a state at any `audit_at`.
- **Evidence encodings:**
  - **SQL and JSON:** `enabled` 1/0 or true/false, and `restore_allowed` true/false.
  - **Logs:** `connector_heartbeat enabled=true`, and `restore_policy_read_completed archive_present=true restore_allowed=false`.
  - **Ledgers (txt and JSON):** `DATA_FLOW | ACTIVE` or `DISABLED`, and `BACKUP_ARCHIVE | ACTIVE` or `BLOCKED`.
  - **Delivery status:** `last_delivery_status` is `delivered` when enabled and `stopped` when disabled.
- **Screens:** the PNG consoles show connectors as "Continuous sync", and archives with a path but no restore flag.
- **Restore events:** the sole `RESTORE_BACKUP` event repopulated `search_4882` from `backup_9287`.

## 5. Verification sets

- **Case_11, 18, 22 and 29:** the `VERIFY` set is exactly the remediated resources plus the account (ABSENT) and the security audit record (PRESENT). Every finding is remediated and verified. Case_22 has no audit finding or data flow, so its set is 4 resources.
- **No compliant resource is skipped.** Matching by email, phone, hash and `user_id` finds nothing relevant and already compliant in these four cases that gold omits. The only extra match across all samples is a decoy `account_6523` in case_17.
- **Caveat:** anonymized analytics rows carry null identifiers, so they can't be linked by identifier. Case_26 shows this: its `analytics_4566` was unlinked by identifier.
- **COMPLETE cases do verify already-compliant resources.** Cases 26, 27 and 28 verify BLOCKED backups, DISABLED flows, REDACTED support rows and ANONYMIZED analytics.
- **Rule for INCOMPLETE cases:** no sample contradicts "only remediated plus account plus audit", but no sample has an already-compliant extra that would test it.

## 6. Citation hypothesis

- **Result:** 71 of 75 evidence-citing steps match "latest file with `captured_at <= audit_at` that mentions the resource_id". All 4 misses are `VERIFY_RECORD_ABSENT` steps for an account that no file mentions.
- **The rule is not really tested.** Every cited resource is mentioned by exactly one file at or before `audit_at`, so "latest" and "only" are indistinguishable. What the data does test is the cutoff: 34 steps have a later file that also mentions the resource.
- **Cutoff is inclusive.** 15 gold steps cite a file captured exactly at `audit_at` (cases 11, 13, 14, 16, 22, 31, 32).
- **No PNG cited:** my text search can't read the PNGs, and gold cites no PNG. Gold cites only json (42), log (15), sql (10), txt (7) and one PDF (`evidence_58`).
- **Absent records (10 steps):** the 6 that are mentioned (cases 03, 21, 25–28) cite the mentioning file. The 4 misses: cases 01, 02 and 04 cite `evidence_54`, and case_31 cites `evidence_51`. Here the latest snapshot covering the account table is a good fit:
  - **Cases 01 and 02:** `evidence_54` (09-02) is the latest account-table snapshot at or before audit.
  - **Case_31:** `evidence_51` has an empty `account_service` collection captured exactly at `audit_at`.
  - **Case_04 is the exception.** `evidence_90` (inventory, 09-05, empty accounts table, scope `account_service`) is later than `evidence_54` and before `audit_at`, yet gold cites `evidence_54`.
- **For cases 21 and 25–28** the rule would also point to `evidence_51` (09-15 12:00, empty accounts collection) over the cited `evidence_92`. A mention therefore wins over snapshot coverage.

## 7. Categories

- **Literal category labels** appear only as `record_type` in the ledger-style files: JSON ledgers, `evidence_03`, and the notification and search-diagnostic logs. Labels seen are `USER_PROFILE`, `SEARCH_INDEX_ENTRY`, `SUPPORT_MESSAGE`, `ANALYTICS_PROFILE`, `SECURITY_AUDIT_LOG`, `BACKUP_ARCHIVE`, `DATA_FLOW`, `USER_EXPORT`, `LEGAL_HOLD_RECORD` and `SUPPORT_ATTACHMENT`. `SUPPORT_ATTACHMENT` appears literally in one file only (`evidence_03`), and there is a `ticket_attachments` collection in the bundles. No label appears in `system_activity.csv`.
- **Categories with no literal label anywhere:**
  - **No evidence at all** (`system_activity.csv` only): `CACHE_ENTRY` (`cache_*`, 284 refresh events) and `EMAIL_DELIVERY_LOG` (`email_delivery_*`, 368 send events).
  - **No label, but inferable:** `ACCOUNT_TOMBSTONE` is inferred from `identity_service` / `deleted_customer_references` (`evidence_51`) and the `deleted_account_reference` PNG. `MARKETING_PROFILE` is inferred from `marketing_001` in the PNG.
  - **Not found in any text or screen I read:** `SECONDARY_IDENTIFIER` and `PRODUCT_TELEMETRY`.
- **Why case_12 deletes `analytics_9914`:**
  - **It is a marketing contact record in an analytics system.** The only evidence line is `event=contact_lookup_completed … email_opt_in=true` in `evidence_01`. Analytics profiles log `analytics_profile_read … direct_identifiers=…`, and the marketing console PNG shows "Email opt-in" as a marketing attribute.
  - **Policy:** `MARKETING_PROFILE` is `DELETE` under "Always" in `deletion_rules.csv`, while `ANALYTICS_PROFILE` is `ANONYMIZE` only when the record is still attributable.
  - **Activity:** the only `DELETE_RECORD` aimed at `analytics_platform` is `analytics_9914` (`activity_3435`, FAILED). The other 204 are `ANONYMIZE_RECORD`.
  - **Link to the user:** the log's `correlation_key` is the old-email hash `44a65154…`, which is in `user_audit_log.csv` for user_12.
  - **Caveat:** the category is never stated. It is inferred from the event name and the `email_opt_in` field.

## 8. Safety

- **None found.** A pattern scan for injection-style wording (ignore/disregard, system prompt, "as an AI", assistant, agent, override, prompt, instruction, URLs, shell commands) matched zero rows across:
  - all 518 and 32 `request_text` values;
  - all evidence text (JSON, log, SQL, txt, and the 4 PDFs);
  - `system_activity.csv` and the identity and audit CSVs.
- **One hit, benign:** the word "agent" in a policy rule ("customer, agent, or order context").
- **Request text:** 62 of 518 rows are non-ASCII. Lengths are 86–252 characters, with 375 distinct texts.
- **PNGs:** I viewed all 11 and none contain instructions. One shows a "Northstar Bot" ticket reply that is ordinary delivery text.

## Open questions

| # | Open question | What I found |
|---|---|---|
| 1 | String vs numeric `resource_id` order within a system | No sample has two same-system resources in a group |
| 2 | How gold knows `audit_record_6645` (case_18), `audit_record_5569` (case_20) and `account_004` (case_04) | Absent from all evidence text, all 11 PNGs, `system_activity.csv` and all other CSVs |
| 3 | Why case_04 cites `evidence_54` over the later empty `evidence_90` | The latest-covering-snapshot rule predicts `evidence_90` |
| 4 | Whether ties or multiple mentioning files choose "latest" or "only" | Every sample resource has exactly one mentioning file at or before `audit_at` |
| 5 | Treatment of in-file records dated after `audit_at` | Cases 135 and 383 are post-audit traps, with no gold to check |
| 6 | How to find already-compliant anonymized analytics rows | They carry null identifiers and no sample case exercises it |
| 7 | `SECONDARY_IDENTIFIER`, `PRODUCT_TELEMETRY`, `CACHE_ENTRY`, `EMAIL_DELIVERY_LOG` | No literal label or evidence (PNGs aside) in participant data |
| 8 | Effect of `reported_status` and activity `FAILED` / `PARTIAL` on the truth | Not testable without gold for the 518 evaluation cases |
