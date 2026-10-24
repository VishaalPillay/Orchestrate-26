"""JSON evidence: ledgers, snapshot bundles and service resource exports.

Three shapes seen in recon:
  * ledger (connector_configurations, export_manifest, security_audit_ledger):
    per-record system, resource_id, record_type, subject_reference, recorded_at.
  * bundle (customer_data_snapshot_bundle): snapshots[] of data_store /
    collection / records[] with captured_at. An empty collection yields a
    coverage ABSENCE fact (case_31 cites evidence_51 this way).
  * service_resource_export: service_exports.<service>.<collection>[] with
    observed_at per record.
A per-record NOT_FOUND observation yields a resource-level ABSENCE fact.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pcda.facts import Fact
from pcda.ingest.base import ManifestRow


class JsonShape(str, Enum):
    LEDGER = "ledger"
    BUNDLE = "bundle"
    SERVICE_EXPORT = "service_export"


def detect_shape(doc: Any) -> JsonShape:
    raise NotImplementedError("json_adapter.detect_shape")


class JsonAdapter:
    def parse(self, path: Path, row: ManifestRow) -> list[Fact]:
        raise NotImplementedError("json_adapter.JsonAdapter.parse")

    def _parse_ledger(self, doc: Any, row: ManifestRow) -> list[Fact]:
        raise NotImplementedError("json_adapter._parse_ledger")

    def _parse_bundle(self, doc: Any, row: ManifestRow) -> list[Fact]:
        raise NotImplementedError("json_adapter._parse_bundle")

    def _parse_service_export(self, doc: Any, row: ManifestRow) -> list[Fact]:
        raise NotImplementedError("json_adapter._parse_service_export")
