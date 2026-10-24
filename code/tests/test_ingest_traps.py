"""The four ingest traps the solver depends on, each on a verbatim dataset snippet.

  * case_135 / case_383: a file captured at T carries a FOUND record observed
    after T (evidence_13, evidence_36). as_of(audit_at) must hide it.
  * case_17: the identifier and the resource_id sit on different log lines and
    are joined only by trace_id (evidence_01, tr-4708586 -> account_4092).
  * case_05: account_005 exists only in the failover replica (evidence_54).
  * case_31: an empty account collection is coverage, not silence (evidence_51).
"""

from __future__ import annotations

import pytest

from pcda.facts import ATTR_COVERAGE, ATTR_LINKED, ATTR_PII_FIELDS, ATTR_STATE, ATTR_TABLE, FactKind, FactStore, parse_ts
from pcda.ingest.json_adapter import JsonAdapter
from pcda.ingest.log_adapter import LogAdapter
from pcda.ingest.sql_adapter import SqlAdapter

# --- case_135: evidence_13 (connector_configurations_20260812.json), records trimmed to the trap ---
EVIDENCE_13 = """{
  "exported_at": "2026-08-12T09:00:00Z",
  "origin": "integration_management_service",
  "record_count": 3,
  "records": [
    {"recorded_at": "2026-08-12T09:00:00Z", "system": "account_service", "resource_id": "account_1084",
     "record_type": "USER_PROFILE", "result": "NOT_FOUND", "subject_reference": "mateo.alvarez@gmail.com"},
    {"recorded_at": "2026-08-12T09:00:00Z", "system": "account_service", "resource_id": "account_1417",
     "record_type": "USER_PROFILE", "result": "NOT_FOUND", "subject_reference": "o.morgan56@gmail.com"},
    {"recorded_at": "2026-08-14T09:00:00Z", "system": "account_service", "resource_id": "account_1417",
     "record_type": "USER_PROFILE", "result": "FOUND", "subject_reference": "o.morgan56@gmail.com"}
  ]
}"""

# --- case_383: evidence_36 (customer_export_manifest_20260409.json) ---
EVIDENCE_36 = """{
  "exported_at": "2026-04-09T12:00:00Z",
  "origin": "customer_export_service",
  "record_count": 3,
  "records": [
    {"recorded_at": "2026-04-09T12:00:00Z", "system": "account_service", "resource_id": "account_5043",
     "record_type": "USER_PROFILE", "result": "NOT_FOUND", "subject_reference": "mathis-girard@proton.me"},
    {"recorded_at": "2026-04-09T12:00:00Z", "system": "account_service", "resource_id": "account_4441",
     "record_type": "USER_PROFILE", "result": "NOT_FOUND", "subject_reference": "LDAHL9787@FASTMAIL.COM"},
    {"recorded_at": "2026-04-11T12:00:00Z", "system": "account_service", "resource_id": "account_5043",
     "record_type": "USER_PROFILE", "result": "FOUND", "subject_reference": "mathis-girard@proton.me"}
  ]
}"""

# --- case_17: evidence_01 (service_events_ap_south_20260915.log) lines 11-21, in file order ---
EVIDENCE_01_LINES = """\
2026-09-15T11:50:57Z level=DEBUG service=support_platform instance=support-2 pod=support-70f0 region=ap-south-1 trace_id=tr-8896806 event=message_scan_started subject_key="e.varga90@yahoo.com" attempt=1
2026-09-15T11:51:04Z level=DEBUG service=support_platform instance=support-2 pod=support-70f0 region=ap-south-1 trace_id=tr-8896806 event=connection_acquired pool=support_platform_replica wait_ms=49
2026-09-15T11:51:08Z level=WARN service=support_platform instance=support-2 pod=support-70f0 region=ap-south-1 trace_id=tr-8896806 resource_id=support_9553 event=message_pii_scan matched_value="e.varga90@yahoo.com" detector=direct_identifier
2026-09-15T11:51:09Z level=DEBUG service=account_service instance=account-6 pod=account-581a region=us-east-2 trace_id=tr-4708586 event=profile_lookup_started lookup_key="leonie.g9352@outlook.com" attempt=1
2026-09-15T11:51:15Z level=DEBUG service=account_service instance=account-6 pod=account-581a region=us-east-2 trace_id=tr-4708586 event=connection_acquired pool=account_service_replica wait_ms=17
2026-09-15T11:51:16Z level=INFO service=search_service instance=search-7 pod=search-acaf region=eu-central-1 trace_id=tr-0506485 resource_id=search_4639 event=index_query_completed index=customer_directory_v3 result=hit shard=7 took_ms=23 correlation_key="darwish.omar@icloud.com"
2026-09-15T11:51:17Z level=WARN service=account_service instance=account-6 pod=account-581a region=us-east-2 trace_id=tr-4708586 event=dependency_retry dependency=account_service_store attempt=1 retry_in_ms=412 reason=read_timeout
2026-09-15T11:51:18Z level=INFO service=account_service instance=account-6 pod=account-581a region=us-east-2 trace_id=tr-4708586 event=dependency_recovered attempt=2 route=primary
2026-09-15T11:51:19Z level=INFO service=account_service instance=account-6 pod=account-581a region=us-east-2 trace_id=tr-4708586 resource_id=account_4092 event=profile_lookup_completed rows=0 http_status=404 duration_ms=33
2026-09-15T11:51:20Z level=INFO service=account_service instance=account-1 pod=account-b3b8 region=ap-south-1 trace_id=tr-9146590 resource_id=account_4745 event=profile_lookup_completed rows=0 http_status=404 duration_ms=22 correlation_key="lucia-torres@gmail.com"
"""

# --- case_05: evidence_54 (customer_accounts_failover_replica_20260902.sql), verbatim ---
EVIDENCE_54 = """\
-- Ghost Data Still Exists: point-in-time database snapshot
-- Dialect: SQLite-compatible SQL
-- Captured at: 2026-09-02T23:00:00Z
-- Database topology: failover_replica
-- Compare the primary and any replica tables for replication drift.

BEGIN TRANSACTION;
CREATE TABLE customer_profiles_failover_replica (
                resource_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL,
                account_status TEXT NOT NULL, updated_at TEXT NOT NULL
            );
INSERT INTO "customer_profiles_failover_replica" VALUES('account_005','user_05','Leila Haddad','leila.haddad@yahoo.com','+212 6 48 27 15 93','ACTIVE','2026-09-02T22:45:00Z');
INSERT INTO "customer_profiles_failover_replica" VALUES('account_8741','user_8741','Karim El-Masri','karim.elmasri@gmail.com','+971 50 731 6284','ACTIVE','2026-09-02T22:45:00Z');
INSERT INTO "customer_profiles_failover_replica" VALUES('account_9116','user_9116','Mateo Rios Gil','m.rios.gil@outlook.com','+34 628 491 736','ACTIVE','2026-09-02T22:45:00Z');
INSERT INTO "customer_profiles_failover_replica" VALUES('account_7204','user_7204','Priya Ramanathan','p.ramanathan@gmail.com','+91 90000 82014','ACTIVE','2026-09-02T22:45:00Z');
CREATE TABLE customer_profiles_primary (
                resource_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL,
                account_status TEXT NOT NULL, updated_at TEXT NOT NULL
            );
INSERT INTO "customer_profiles_primary" VALUES('account_8741','user_8741','Karim El-Masri','karim.elmasri@gmail.com','+971 50 731 6284','ACTIVE','2026-09-02T22:45:00Z');
INSERT INTO "customer_profiles_primary" VALUES('account_9116','user_9116','Mateo Rios Gil','m.rios.gil@outlook.com','+34 628 491 736','ACTIVE','2026-09-02T22:45:00Z');
INSERT INTO "customer_profiles_primary" VALUES('account_7204','user_7204','Priya Ramanathan','p.ramanathan@gmail.com','+91 90000 82014','ACTIVE','2026-09-02T22:45:00Z');
COMMIT;
"""

# --- case_31: evidence_51 (customer_data_snapshot_bundle_20260915.json), verbatim ---
EVIDENCE_51 = """{
  "exported_at": "2026-09-15T12:00:00Z",
  "source": "records_assurance_service",
  "snapshots": [
    {"captured_at": "2026-09-15T12:00:00Z", "data_store": "account_service",
     "collection": "customer_profiles", "records": []},
    {"captured_at": "2026-09-15T12:00:00Z", "data_store": "identity_service",
     "collection": "deleted_customer_references",
     "records": [
       {"resource_id": "tombstone_440", "subject_reference": "user_541", "name": null, "email": null,
        "phone": null, "email_hash": null, "account_status": "DELETED"},
       {"resource_id": "tombstone_9178", "subject_reference": "user_542", "name": "Kofi Addo",
        "email": "addo.kofi@gmail.com", "phone": "+233 24 991 1328",
        "email_hash": "4f4b9c98a05a2dad20e52313cce7809d18c3da544754dcd46d728b0173174b26",
        "account_status": "DELETED"}
     ]},
    {"captured_at": "2026-09-15T12:00:00Z", "data_store": "security_service",
     "collection": "security_audit_events",
     "records": [
       {"resource_id": "audit_record_6909", "subject_reference": "user_541",
        "event_name": "account_deletion_reviewed", "actor_type": "automated_privacy_workflow",
        "integrity_seal": "sha256:0000000000000000000000000000000000000000000000000000000000001afd"}
     ]}
  ]
}"""


@pytest.mark.parametrize(
    "name, text, file_id, captured, audit_at, resource, email",
    [
        ("evidence_13.json", EVIDENCE_13, "evidence_13", "2026-08-12T09:00:00Z", "2026-08-12T09:00:00Z", "account_1417", "o.morgan56@gmail.com"),
        ("evidence_36.json", EVIDENCE_36, "evidence_36", "2026-04-09T12:00:00Z", "2026-04-09T12:00:00Z", "account_5043", "mathis-girard@proton.me"),
    ],
    ids=["case_135", "case_383"],
)
def test_record_observed_after_audit_is_hidden(
    write_evidence, manifest_row, report, name, text, file_id, captured, audit_at, resource, email
):
    row = manifest_row(file_id, captured)
    facts = JsonAdapter(report).parse(write_evidence(name, text), row)
    store = FactStore(facts)

    # Before as_of both observations exist; the FOUND one is later than the file's capture time.
    all_for_resource = store.by_resource(resource)
    assert [f.kind for f in all_for_resource] == [FactKind.ABSENCE, FactKind.PRESENCE]
    found = all_for_resource[-1]
    assert found.observed_at > found.captured_at

    visible = store.as_of(parse_ts(audit_at))
    kinds = [f.kind for f in visible.by_resource(resource)]
    assert kinds == [FactKind.ABSENCE]
    assert FactKind.PRESENCE not in {f.kind for f in visible.by_identifier(email)}
    # The manifest, not the content, is the capture time.
    assert all(f.captured_at == row.captured_at for f in facts)
    assert report.warnings == []


def test_in_file_time_that_disagrees_with_manifest_is_logged_not_applied(write_evidence, manifest_row, report):
    row = manifest_row("evidence_13", "2026-08-13T00:00:00Z")  # manifest differs from exported_at
    facts = JsonAdapter(report).parse(write_evidence("e13.json", EVIDENCE_13), row)
    assert all(f.captured_at == row.captured_at for f in facts)
    assert any("manifest" in w and "evidence_13" in w for w in report.warnings)


def test_case17_trace_joins_identifier_to_resource(write_evidence, manifest_row, report):
    row = manifest_row("evidence_01", "2026-09-15T11:59:39Z")
    facts = LogAdapter(report).parse(write_evidence("evidence_01.log", EVIDENCE_01_LINES), row)
    store = FactStore(facts)

    lookups = store.by_resource("account_4092")
    absence = [f for f in lookups if f.kind is FactKind.ABSENCE]
    assert len(absence) == 1
    assert "leonie.g9352@outlook.com" in absence[0].identifiers  # from the `_started` line
    assert absence[0].attrs[ATTR_STATE] == "NOT_FOUND"
    assert absence[0].attrs["http_status"] == "404"
    assert absence[0].attrs["trace_id"] == "tr-4708586"

    link = [f for f in lookups if f.kind is FactKind.LINK]
    assert [f.attrs[ATTR_LINKED] for f in link] == ["tr-4708586"]
    assert "leonie.g9352@outlook.com" in link[0].identifiers

    # Interleaved traces must not bleed into each other.
    assert "e.varga90@yahoo.com" not in absence[0].identifiers
    assert "darwish.omar@icloud.com" not in absence[0].identifiers
    assert {f.resource_id for f in store.by_identifier("leonie.g9352@outlook.com")} == {"account_4092"}
    # The other account lookup in the same file keeps its own correlation_key.
    other = [f for f in store.by_resource("account_4745") if f.kind is FactKind.ABSENCE][0]
    assert other.identifiers == frozenset({"lucia-torres@gmail.com"})


def test_trace_less_line_is_read_on_its_own(write_evidence, manifest_row, report):
    # evidence_61 (support_replica.log): no trace_id, identifier and resource on one line.
    text = (
        "2026-09-17T22:00:11Z level=INFO service=support_replica instance=support-eu-03 pod=replica-7c9f "
        "event=contact_query_completed resource_id=support_5914 lookup_key=nadia.popescu@yahoo.com "
        "rows=1 source=case_archive latency_ms=31\n"
    )
    facts = LogAdapter(report).parse(write_evidence("evidence_61.log", text), manifest_row("evidence_61", "2026-09-17T22:00:19Z"))
    assert [(f.kind, f.resource_id) for f in facts] == [(FactKind.PRESENCE, "support_5914")]
    assert facts[0].identifiers == frozenset({"nadia.popescu@yahoo.com"})


def test_failover_drift_account_005_only_in_replica(write_evidence, manifest_row, report):
    row = manifest_row("evidence_54", "2026-09-02T23:00:00Z")
    facts = SqlAdapter(report).parse(write_evidence("evidence_54.sql", EVIDENCE_54), row)
    store = FactStore(facts)

    rows_005 = store.by_resource("account_005")
    assert [(f.kind, f.attrs[ATTR_TABLE]) for f in rows_005] == [
        (FactKind.PRESENCE, "customer_profiles_failover_replica")
    ]
    assert rows_005[0].topology == "failover_replica"
    assert rows_005[0].system_id == "account_service"
    assert rows_005[0].identifiers == frozenset({"user_05", "leila.haddad@yahoo.com", "212648271593"})
    assert rows_005[0].attrs[ATTR_PII_FIELDS] == "full_name,email,phone"

    # The primary was captured too: it covers account_service and does not hold account_005.
    coverage = {f.attrs[ATTR_TABLE] for f in store.coverage()}
    assert coverage == {"customer_profiles_primary", "customer_profiles_failover_replica"}
    # The three shared accounts exist in both tables.
    for rid in ("account_8741", "account_9116", "account_7204"):
        assert {f.attrs[ATTR_TABLE] for f in store.by_resource(rid)} == coverage
    assert report.warnings == []


def test_empty_account_collection_is_coverage(write_evidence, manifest_row, report):
    row = manifest_row("evidence_51", "2026-09-15T12:00:00Z")
    facts = JsonAdapter(report).parse(write_evidence("evidence_51.json", EVIDENCE_51), row)
    store = FactStore(facts)

    (cov,) = store.coverage()
    assert cov.kind is FactKind.ABSENCE and cov.is_coverage
    assert cov.resource_id is None
    assert cov.system_id == "account_service"
    assert cov.attrs[ATTR_TABLE] == "customer_profiles"
    assert cov.attrs[ATTR_COVERAGE] == "true"
    assert "is_vacuous" not in cov.attrs  # other collections in the same bundle hold records
    assert cov.observed_at == cov.captured_at == parse_ts("2026-09-15T12:00:00Z")
    assert store.as_of(parse_ts("2026-09-15T12:00:00Z")).coverage() == [cov]  # inclusive cutoff
    assert store.as_of(parse_ts("2026-09-15T11:59:59Z")).coverage() == []


def test_tombstone_records_keep_reference_and_report_remaining_pii(write_evidence, manifest_row, report):
    row = manifest_row("evidence_51", "2026-09-15T12:00:00Z")
    store = FactStore(JsonAdapter(report).parse(write_evidence("evidence_51.json", EVIDENCE_51), row))

    clean = store.by_resource("tombstone_440")[0]
    assert clean.identifiers == frozenset({"user_541"})
    assert ATTR_PII_FIELDS not in clean.attrs

    dirty = store.by_resource("tombstone_9178")[0]
    assert dirty.identifiers == frozenset(
        {
            "user_542",
            "addo.kofi@gmail.com",
            "233249911328",  # digits only
            "4f4b9c98a05a2dad20e52313cce7809d18c3da544754dcd46d728b0173174b26",
        }
    )
    assert dirty.attrs[ATTR_PII_FIELDS] == "name,email,phone,email_hash"
