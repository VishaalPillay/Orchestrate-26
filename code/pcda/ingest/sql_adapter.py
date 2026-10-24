"""SQL snapshot evidence, executed into an in-memory sqlite database.

Topology comes from the `-- Database topology:` header when present
(read_replica, regional_replicas, reporting_replica, failover_replica,
primary_only). The 11 customer_data_inventory_* files have no header; their
scope is the snapshot_scope table's origin_system rows.

Per table the adapter emits row facts (PRESENCE / PII_* / FLOW_STATE /
RESTORE_STATE / LINK) and one coverage ABSENCE fact (resource_id None,
attrs table + coverage). Coverage facts carry is_vacuous when the snapshot
has no data rows at all (evidence_90: one INSERT, into snapshot_scope only).

The manifest captured_at is authoritative; an in-file capture time that
disagrees becomes an IngestReport warning.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pcda.facts import Fact
from pcda.ingest.base import ManifestRow


@dataclass(frozen=True)
class SnapshotHeader:
    captured_at: datetime | None
    topology: str | None
    scope_systems: tuple[str, ...]


def load_snapshot(path: Path) -> sqlite3.Connection:
    raise NotImplementedError("sql_adapter.load_snapshot")


def read_header(path: Path, conn: sqlite3.Connection) -> SnapshotHeader:
    raise NotImplementedError("sql_adapter.read_header")


class SqlAdapter:
    def parse(self, path: Path, row: ManifestRow) -> list[Fact]:
        raise NotImplementedError("sql_adapter.SqlAdapter.parse")

    def _table_facts(
        self, conn: sqlite3.Connection, table: str, header: SnapshotHeader, row: ManifestRow
    ) -> list[Fact]:
        raise NotImplementedError("sql_adapter._table_facts")
