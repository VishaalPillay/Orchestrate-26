"""Adapter behaviour per format, on verbatim snippets (log styles, txt, normalization, images)."""

from __future__ import annotations

import pytest

from pcda import paths
from pcda.facts import ATTR_STATE, FactKind, FactStore
from pcda.ingest.base import IngestReport, ManifestRow, SkippedEvidence
from pcda.ingest.image_adapter import ImageAdapter
from pcda.ingest.json_adapter import JsonAdapter
from pcda.ingest.log_adapter import LogAdapter, LogStyle, detect_style, parse_line
from pcda.ingest.txt_adapter import TxtAdapter
from pcda.llm.client import RunMode

# One real line per log style: (file, style, line, expected event, expected trace_id)
LOG_LINES = [
    (
        "evidence_01",
        LogStyle.KEY_VALUE,
        '2026-09-15T11:51:19Z level=INFO service=account_service instance=account-6 pod=account-581a region=us-east-2 trace_id=tr-4708586 resource_id=account_4092 event=profile_lookup_completed rows=0 http_status=404 duration_ms=33',
        "profile_lookup_completed",
        "tr-4708586",
    ),
    (
        "evidence_06",
        LogStyle.JSON_PREFIXED,
        '2026-09-10T08:56:20Z {"severity":"DEBUG","logger":"account_service","service":"account_service","instance":"account-6","pod":"account-ca1e","region":"us-east-2","trace_id":"tr-5563868","resource_id":"account_6633","event":"profile_lookup_completed","rows":"1","http_status":"200","duration_ms":"84"}',
        "profile_lookup_completed",
        "tr-5563868",
    ),
    (
        "evidence_11",
        LogStyle.SYSLOG,
        '2026-08-22T10:51:17Z <15>1 node-1 account_service tr-4973247 - - level=DEBUG instance="account-2" pod="account-0525" region="eu-west-1" resource_id="account_1785" event="profile_lookup_completed" rows="1" http_status="200" duration_ms="50"',
        "profile_lookup_completed",
        "tr-4973247",
    ),
    (
        "evidence_16",
        LogStyle.BRACKETED_TRACE,
        '2026-07-28T05:51:21Z [INFO] [account_service] [tr-6074163] profile_lookup_completed: instance="account-5" pod="account-886d" region="eu-west-1" resource_id="account_7947" rows="0" http_status="404" duration_ms="70"',
        "profile_lookup_completed",
        "tr-6074163",
    ),
    (
        "evidence_21",
        LogStyle.PIPE_DELIMITED,
        '2026-07-03T07:51:32Z | DEBUG | security_service | tr-5156005 | audit_record_read_completed | instance="security-6" pod="security-7e9b" region="eu-central-1" resource_id="audit_record_4413" integrity_check="passed" rows="1" correlation_key="user_211"',
        "audit_record_read_completed",
        "tr-5156005",
    ),
    (
        "evidence_25",
        LogStyle.CLOUD_EXPORT,
        '2026-06-08T06:52:09Z requestId=tr-1108860 level=INFO function=search_service message="instance=\\"search-3\\" pod=\\"search-0696\\" region=\\"us-east-2\\" resource_id=\\"search_021\\" event=\\"index_query_completed\\" index=\\"customer_directory_v3\\" result=\\"hit\\" shard=\\"9\\" took_ms=\\"24\\" correlation_key=\\"mrios9507.jr@fastmail.com\\""',
        "index_query_completed",
        "tr-1108860",
    ),
]


@pytest.mark.parametrize("_, style, line, event, trace", LOG_LINES, ids=[s.value for _, s, *_ in LOG_LINES])
def test_each_log_style_is_detected_and_parsed(_, style, line, event, trace):
    assert detect_style([line]) is style
    parsed = parse_line(style, 1, line)
    assert parsed is not None
    assert parsed.fields["event"] == event
    assert parsed.fields["trace_id"] == trace
    assert parsed.fields["resource_id"].split("_")[0] in {"account", "audit", "search"}


def test_cloud_export_message_is_unwrapped_and_correlation_key_is_normalized(
    write_evidence, manifest_row, report
):
    line = LOG_LINES[5][2]
    facts = LogAdapter(report).parse(write_evidence("e25.log", line + "\n"), manifest_row("evidence_25", "2026-06-08T10:00:00Z"))
    presence = [f for f in facts if f.kind is FactKind.PRESENCE]
    assert len(presence) == 1
    assert presence[0].system_id == "search_service"
    assert presence[0].identifiers == frozenset({"mrios9507.jr@fastmail.com"})


def test_lookup_outcome_decides_presence_or_absence(write_evidence, manifest_row, report):
    text = (
        '2026-07-28T05:51:18Z [DEBUG] [account_service] [tr-6074163] profile_lookup_started: instance="account-5" lookup_key="L.Nascimento40@Gmail.com" attempt="1"\n'
        '2026-07-28T05:51:21Z [INFO] [account_service] [tr-6074163] profile_lookup_completed: instance="account-5" resource_id="account_7947" rows="0" http_status="404" duration_ms="70"\n'
        '2026-07-28T05:52:00Z [DEBUG] [account_service] [tr-1111111] profile_lookup_started: instance="account-5" lookup_key="kept@example.com" attempt="1"\n'
        '2026-07-28T05:52:03Z [INFO] [account_service] [tr-1111111] profile_lookup_completed: instance="account-5" resource_id="account_1" rows="1" http_status="200" duration_ms="9"\n'
    )
    facts = LogAdapter(report).parse(write_evidence("e16.log", text), manifest_row("evidence_16", "2026-07-30T00:00:00Z"))
    by_kind = {(f.kind, f.resource_id) for f in facts if f.kind is not FactKind.LINK}
    assert by_kind == {(FactKind.ABSENCE, "account_7947"), (FactKind.PRESENCE, "account_1")}
    assert FactStore(facts).by_identifier("l.nascimento40@gmail.com")  # lowercased at ingest


def test_txt_handover_rows(write_evidence, manifest_row, report):
    text = (
        "CUSTOMER PLATFORM - SHIFT HANDOVER\nSnapshot: 2026-10-01T12:00:00Z\n\n"
        "Account and downstream checks completed during the shift:\n\n"
        "- 2026-10-01T12:00:00Z | account_service | account_1810 | USER_PROFILE | NOT_FOUND | CHOI.JIHOON94@GMAIL.COM\n"
        "- 2026-10-01T12:00:00Z | support_platform | support_2667 | SUPPORT_MESSAGE | REDACTED | user_39\n"
        "- 2026-10-01T12:00:00Z | support_platform | support_4887 | SUPPORT_MESSAGE | IDENTIFIABLE | CHOI.JIHOON94@GMAIL.COM\n"
        "- 2026-10-01T12:00:00Z | integration_service | data_flow_9522 | DATA_FLOW | ACTIVE | novak.tomasz@icloud.com\n"
        "- 2026-10-01T12:00:00Z | backup_service | backup_1001 | BACKUP_ARCHIVE | BLOCKED | user_40\n"
    )
    facts = TxtAdapter(report).parse(write_evidence("e3.txt", text), manifest_row("evidence_03", "2026-10-01T12:00:00Z"))
    assert [(f.kind, f.resource_id, f.attrs[ATTR_STATE]) for f in facts] == [
        (FactKind.ABSENCE, "account_1810", "NOT_FOUND"),
        (FactKind.PII_CLEARED, "support_2667", "REDACTED"),
        (FactKind.PII_EXPOSED, "support_4887", "IDENTIFIABLE"),
        (FactKind.FLOW_STATE, "data_flow_9522", "ACTIVE"),
        (FactKind.RESTORE_STATE, "backup_1001", "BLOCKED"),
    ]
    assert facts[0].identifiers == frozenset({"choi.jihoon94@gmail.com"})
    assert facts[0].category == "USER_PROFILE" and facts[3].category == "DATA_FLOW"
    assert report.warnings == []


def test_bundle_state_records_and_phone_normalization(write_evidence, manifest_row, report):
    doc = """{
      "exported_at": "2026-09-17T12:00:00Z", "source": "records_assurance_service",
      "snapshots": [
        {"captured_at": "2026-09-15T12:00:00Z", "data_store": "account_service", "collection": "customer_profiles",
         "records": [{"resource_id": "account_5334", "customer_reference": "user_24", "full_name": "Keiko Nakamura",
                      "email": "Keiko.Nakamura@gmail.com", "phone": "+81 90 2669 6684", "account_status": "ACTIVE",
                      "signup_channel": "referral"}]},
        {"captured_at": "2026-09-15T12:00:00Z", "data_store": "backup_service", "collection": "backup_catalog",
         "records": [{"resource_id": "backup_1", "subject_reference": "user_24", "archive_path": "s3://customer-backups/weekly/segment-22.tar.zst",
                      "encryption": "AES256", "restore_allowed": false}]},
        {"captured_at": "2026-09-15T12:00:00Z", "data_store": "integration_service", "collection": "connector_configurations",
         "records": [{"resource_id": "data_flow_1", "connector_name": "analytics_identity_bridge", "destination": "customer_analytics_warehouse",
                      "mode": "continuous_sync", "subject_selector": "user_24", "enabled": true}]}
      ]}"""
    facts = JsonAdapter(report).parse(write_evidence("b.json", doc), manifest_row("evidence_02", "2026-09-17T12:00:00Z"))
    by_rid = {f.resource_id: f for f in facts}
    assert by_rid["account_5334"].identifiers == frozenset({"user_24", "keiko.nakamura@gmail.com", "819026696684"})
    assert (by_rid["backup_1"].kind, by_rid["backup_1"].attrs[ATTR_STATE]) == (FactKind.RESTORE_STATE, "BLOCKED")
    assert (by_rid["data_flow_1"].kind, by_rid["data_flow_1"].attrs[ATTR_STATE]) == (FactKind.FLOW_STATE, "ACTIVE")
    assert by_rid["account_5334"].observed_at.isoformat() == "2026-09-15T12:00:00+00:00"
    assert not report.warnings


def test_service_export_null_profile_and_404_are_absence(write_evidence, manifest_row, report):
    doc = """{"exported_at": "2026-09-15T11:44:55Z", "region": "global", "service_exports": {
      "account_service": {"profile_lookups": [
        {"observed_at": "2026-09-15T09:31:40Z", "profile": null, "resource_id": "account_3136", "status_code": 404},
        {"observed_at": "2026-09-15T09:41:33Z", "profile": {"customer_reference": null, "email": "keiko.nakamura@gmail.com",
         "email_hash": null, "full_name": null, "phone": null}, "resource_id": "account_5334", "status_code": 200}]}}}"""
    facts = JsonAdapter(report).parse(write_evidence("s.json", doc), manifest_row("evidence_92", "2026-09-15T11:44:55Z"))
    assert [(f.kind, f.resource_id) for f in facts] == [
        (FactKind.ABSENCE, "account_3136"),
        (FactKind.PRESENCE, "account_5334"),
    ]
    assert facts[0].observed_at.isoformat() == "2026-09-15T09:31:40+00:00"  # per-record observed_at
    assert facts[1].identifiers == frozenset({"keiko.nakamura@gmail.com"})


def test_image_is_skipped_and_flagged_in_deterministic_mode(manifest_row, tmp_path):
    report = IngestReport()
    row = manifest_row("evidence_04", "2026-09-26T11:00:00Z")
    png = tmp_path / "console.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    adapter = ImageAdapter(perception=None, mode=RunMode.DETERMINISTIC, report=report)  # type: ignore[arg-type]
    assert adapter.parse(png, row) == []
    assert [s.evidence_id for s in report.skipped] == ["evidence_04"]
    assert isinstance(report.skipped[0], SkippedEvidence)
    assert any("evidence_04" in w for w in report.warnings)


# --- against the real dataset (skipped when dataset/ is absent, e.g. inside code.zip) ---------------

needs_dataset = pytest.mark.skipif(not paths.EVIDENCE_MANIFEST.exists(), reason="dataset/ not present")


def _row(file_id: str) -> ManifestRow:
    from pcda.ingest.base import read_manifest

    return next(r for r in read_manifest() if r.file_id == file_id)


@needs_dataset
def test_inventory_only_covers_systems_named_in_snapshot_scope():
    from pcda.ingest.sql_adapter import SqlAdapter

    report = IngestReport()
    # evidence_63's scope omits export_service, and its customer_exports table is empty.
    row = _row("evidence_63")
    facts = SqlAdapter(report).parse(row.absolute_path, row)
    covered = {f.system_id for f in facts if f.is_coverage}
    assert "export_service" not in covered
    assert {"account_service", "search_service", "security_service"} <= covered
    # evidence_90 holds one INSERT, into snapshot_scope only: a vacuous account snapshot.
    row = _row("evidence_90")
    (only,) = SqlAdapter(report).parse(row.absolute_path, row)
    assert only.is_coverage and only.system_id == "account_service"
    assert only.attrs["is_vacuous"] == "true"
    assert not report.warnings


@needs_dataset
def test_pdf_text_layer_yields_support_attachment_fact():
    from pcda.ingest.pdf_adapter import PdfAdapter

    report = IngestReport()
    row = _row("evidence_58")
    (fact,) = PdfAdapter(None, RunMode.DETERMINISTIC, report).parse(row.absolute_path, row)  # type: ignore[arg-type]
    assert fact.resource_id == "support_001"
    assert {"priya.raman@gmail.com", "919876573142"} <= fact.identifiers
    assert fact.observed_at == row.captured_at
    assert not report.warnings  # the 'captured 12 Sep 2026 07:30 UTC' stamp agrees with the manifest


@needs_dataset
def test_full_ingest_has_unique_ids_and_only_images_are_empty():
    from pcda.agents.perception import LLMPerceptionAgent
    from pcda.ingest.base import read_manifest
    from pcda.ingest.registry import build_adapters, ingest_all

    report = IngestReport()
    manifest = read_manifest()
    store = ingest_all(manifest, build_adapters(LLMPerceptionAgent(None), RunMode.DETERMINISTIC, report), report)  # type: ignore[arg-type]
    populated = {f.evidence_id for f in store}
    empty = {r.file_id for r in manifest} - populated
    assert empty == {r.file_id for r in manifest if r.file_path.endswith(".png")}
    assert {s.evidence_id for s in report.skipped} == empty
    assert [w for w in report.warnings if "image skipped" not in w] == []
    assert all(f.captured_at == next(r.captured_at for r in manifest if r.file_id == f.evidence_id) for f in store)
