"""JSON evidence: ledgers, snapshot bundles, service resource exports and flat collections.

Shapes seen in recon:
  * ledger (connector_configurations, export_manifest, security_audit_ledger):
    per-record system, resource_id, record_type, result, subject_reference,
    recorded_at. `result` is the ledger vocabulary (FOUND, NOT_FOUND,
    IDENTIFIABLE, REDACTED, ANONYMIZED, ACTIVE, DISABLED, BLOCKED).
  * bundle (customer_data_snapshot_bundle): snapshots[] of data_store /
    collection / records[] with captured_at. An empty collection yields a
    coverage ABSENCE fact (case_31 cites evidence_51 this way). Collections
    named operational_record_history hold ledger-style records.
  * service_resource_export: service_exports.<service>.<collection>[] with
    observed_at per record. A null profile or status 404 is ABSENCE.
  * collection (customer_export_jobs, customer_analytics_report): one flat
    {captured_at, source, collection, records[]} document.

observed_at is the record's own timestamp (recorded_at / observed_at /
snapshot captured_at). A file can carry records later than its capture time
(evidence_13, evidence_36); FactStore.as_of hides those by observed_at.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pcda.category import CATEGORIES, DATA_FLOW
from pcda.facts import (
    ATTR_COVERAGE,
    ATTR_ID_FIELDS,
    ATTR_PII_FIELDS,
    ATTR_STATE,
    ATTR_VACUOUS,
    Fact,
    FactKind,
    parse_ts,
)
from pcda.ingest.base import IngestReport, ManifestRow
from pcda.ingest.common import (
    Emitter,
    emit_record,
    fact_attrs,
    result_to_kind,
    view_record,
)

_LABELS = frozenset(CATEGORIES) | {DATA_FLOW}


class JsonShape(str, Enum):
    LEDGER = "ledger"
    BUNDLE = "bundle"
    SERVICE_EXPORT = "service_export"
    COLLECTION = "collection"


def detect_shape(doc: Any) -> JsonShape:
    if not isinstance(doc, dict):
        raise ValueError("JSON evidence must be an object")
    if "service_exports" in doc:
        return JsonShape.SERVICE_EXPORT
    if "snapshots" in doc:
        return JsonShape.BUNDLE
    if "collection" in doc and "records" in doc:
        return JsonShape.COLLECTION
    if "records" in doc:
        return JsonShape.LEDGER
    raise ValueError(f"unrecognised JSON shape (keys: {sorted(doc)})")


class JsonAdapter:
    def __init__(self, report: IngestReport | None = None) -> None:
        self._report = report

    def parse(self, path: Path, row: ManifestRow) -> list[Fact]:
        doc = json.loads(path.read_text(encoding="utf-8"))
        shape = detect_shape(doc)
        Emitter(row, self._report).check_capture(
            doc.get("exported_at") or doc.get("captured_at"), "in-file time"
        )
        if shape is JsonShape.LEDGER:
            return self._parse_ledger(doc, row)
        if shape is JsonShape.BUNDLE:
            return self._parse_bundle(doc, row)
        if shape is JsonShape.SERVICE_EXPORT:
            return self._parse_service_export(doc, row)
        return self._parse_collection(doc, row)

    # -- ledger ------------------------------------------------------------

    def _parse_ledger(self, doc: Mapping[str, Any], row: ManifestRow) -> list[Fact]:
        em = Emitter(row, self._report, src="ledger")
        default = parse_ts(doc.get("exported_at") or row.captured_at.isoformat())
        for rec in doc["records"]:
            self._ledger_record(em, rec, default, table="ledger")
        return em.facts

    def _ledger_record(
        self, em: Emitter, rec: Mapping[str, Any], default: datetime, table: str
    ) -> None:
        record_type = rec.get("record_type")
        mapped = result_to_kind(record_type, rec.get("result"))
        if mapped is None:
            em.warn(f"unmapped ledger result {rec.get('result')!r} for {rec.get('resource_id')}")
            return
        kind, state = mapped
        identity = {k: rec.get(k) for k in ("subject_reference", "lookup_value") if k in rec}
        view = view_record(identity)
        em.emit(
            kind,
            parse_ts(rec["recorded_at"]) if rec.get("recorded_at") else default,
            system_id=rec.get("system"),
            resource_id=rec["resource_id"],
            category=record_type if record_type in _LABELS else None,
            identifiers=view.identifiers,
            table=table,
            **fact_attrs({
                ATTR_STATE: state,
                ATTR_PII_FIELDS: ",".join(view.pii_fields) or None,
                ATTR_ID_FIELDS: ",".join(view.id_fields) or None,
                "record_type": record_type,
                "src": "ledger",
            }),
        )

    # -- bundle ------------------------------------------------------------

    def _parse_bundle(self, doc: Mapping[str, Any], row: ManifestRow) -> list[Fact]:
        em = Emitter(row, self._report, src="db_row")
        snapshots = doc["snapshots"]
        vacuous = not any(s["records"] for s in snapshots)
        for snap in snapshots:
            at = parse_ts(snap["captured_at"])
            store, collection = snap["data_store"], snap["collection"]
            if not snap["records"]:
                self._coverage(em, at, store, collection, vacuous)
            for rec in snap["records"]:
                if "record_type" in rec and "result" in rec:
                    self._ledger_record(em, rec, at, table=collection)
                else:
                    emit_record(
                        em,
                        at,
                        system_id=store,
                        resource_id=rec["resource_id"],
                        table=collection,
                        fields=rec,
                    )
        return em.facts

    # -- service export ----------------------------------------------------

    def _parse_service_export(self, doc: Mapping[str, Any], row: ManifestRow) -> list[Fact]:
        em = Emitter(row, self._report, src="db_row")
        exported = parse_ts(doc["exported_at"])
        services: Mapping[str, Mapping[str, list[Any]]] = doc["service_exports"]
        vacuous = not any(recs for cols in services.values() for recs in cols.values())
        for service in sorted(services):
            for collection in sorted(services[service]):
                records = services[service][collection]
                if not records:
                    self._coverage(em, exported, service, collection, vacuous)
                for rec in records:
                    self._service_record(em, service, collection, rec, exported)
        return em.facts

    def _service_record(
        self, em: Emitter, service: str, collection: str, rec: Mapping[str, Any], default: datetime
    ) -> None:
        at = parse_ts(rec["observed_at"]) if rec.get("observed_at") else default
        rid = rec["resource_id"]
        if collection == "profile_lookups" and (rec.get("profile") is None or rec.get("status_code") == 404):
            em.emit(
                FactKind.ABSENCE,
                at,
                system_id=service,
                resource_id=rid,
                table=collection,
                **fact_attrs({ATTR_STATE: "NOT_FOUND", "status_code": rec.get("status_code")}),
            )
            return
        if collection == "index_queries":
            self._index_query(em, service, collection, rec, at)
            return
        emit_record(em, at, system_id=service, resource_id=rid, table=collection, fields=rec)

    def _index_query(
        self, em: Emitter, service: str, collection: str, rec: Mapping[str, Any], at: datetime
    ) -> None:
        hits = rec.get("hits") or {}
        docs = hits.get("documents") or []
        query = {"query": rec.get("query")}
        if not docs:
            view = view_record(query)
            em.emit(
                FactKind.ABSENCE,
                at,
                system_id=service,
                resource_id=rec["resource_id"],
                identifiers=view.identifiers,
                table=collection,
                **{ATTR_STATE: "NOT_FOUND", "total": hits.get("total", 0)},
            )
            return
        for hit in docs:
            emit_record(
                em,
                at,
                system_id=service,
                resource_id=hit.get("resource_id") or rec["resource_id"],
                table=collection,
                fields={**hit, **query},
                total=hits.get("total"),
            )

    # -- flat collection ---------------------------------------------------

    def _parse_collection(self, doc: Mapping[str, Any], row: ManifestRow) -> list[Fact]:
        em = Emitter(row, self._report, src="db_row")
        at = parse_ts(doc["captured_at"])
        store, collection = doc.get("source"), doc["collection"]
        if not doc["records"]:
            self._coverage(em, at, store, collection, True)
        for rec in doc["records"]:
            emit_record(
                em, at, system_id=store, resource_id=rec["resource_id"], table=collection, fields=rec
            )
        return em.facts

    # -- shared ------------------------------------------------------------

    @staticmethod
    def _coverage(
        em: Emitter, at: datetime, system_id: str | None, table: str, vacuous: bool
    ) -> None:
        attrs: dict[str, Any] = {ATTR_COVERAGE: "true"}
        if vacuous:
            attrs[ATTR_VACUOUS] = "true"
        em.emit(
            FactKind.ABSENCE,
            at,
            system_id=system_id,
            resource_id=None,
            table=table,
            **attrs,
        )
