"""SQL snapshot evidence, executed into an in-memory sqlite database.

Topology comes from the `-- Database topology:` header when present
(read_replica, regional_replicas, reporting_replica, failover_replica,
primary_only). The 11 customer_data_inventory_* files have no header; their
scope is the snapshot_scope table's origin_system rows and their topology is
reported as "inventory".

Per table the adapter emits row facts (PRESENCE / FLOW_STATE / RESTORE_STATE /
LINK) and one coverage ABSENCE fact (resource_id None, attrs table +
coverage). Coverage facts carry is_vacuous when the snapshot has no data rows
at all (evidence_90: one INSERT, into snapshot_scope only). Inventory tables
only give coverage for systems named in snapshot_scope: an empty table for an
out-of-scope system says nothing.

The manifest captured_at is authoritative; an in-file capture time that
disagrees becomes an IngestReport warning.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from pcda.facts import ATTR_COVERAGE, ATTR_VACUOUS, Fact, FactKind, parse_ts
from pcda.ingest.base import IngestReport, ManifestRow
from pcda.ingest.common import Emitter, emit_record

SCOPE_TABLE: Final = "snapshot_scope"
INVENTORY_TOPOLOGY: Final = "inventory"

# Schema-level table -> owning system, for tables without an origin_system
# column. Taken from the snapshot bundles' data_store / collection pairs.
TABLE_SYSTEM: Final[dict[str, str]] = {
    "customer_profiles": "account_service",
    "customer_profiles_primary": "account_service",
    "customer_profiles_failover_replica": "account_service",
    "customer_search_documents": "search_service",
    "search_documents_primary": "search_service",
    "customer_analytics_profiles": "analytics_platform",
    "support_records": "support_platform",
    "customer_exports": "export_service",
    "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service",
    "security_audit_records": "security_service",
}

_TS_COLUMNS: Final = ("updated_at", "indexed_at")
_CAPTURED_RE: Final = re.compile(r"^--\s*Captured at:\s*(\S+)", re.M)
_TOPOLOGY_RE: Final = re.compile(r"^--\s*Database topology:\s*(\S+)", re.M)
_COMPLETED_RE: Final = re.compile(r"^--\s*snapshot_completed_at=(\S+)", re.M)


@dataclass(frozen=True)
class SnapshotHeader:
    captured_at: datetime | None
    topology: str | None
    scope_systems: tuple[str, ...]


def load_snapshot(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(path.read_text(encoding="utf-8"))
    return conn


def _scope_rows(conn: sqlite3.Connection) -> dict[str, str]:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (SCOPE_TABLE,)
    ).fetchone()
    if not exists:
        return {}
    return {
        system: captured
        for system, captured in conn.execute(
            f"SELECT origin_system, captured_at FROM {SCOPE_TABLE} ORDER BY origin_system"
        )
    }


def read_header(path: Path, conn: sqlite3.Connection) -> SnapshotHeader:
    text = path.read_text(encoding="utf-8")
    captured = _CAPTURED_RE.search(text) or _COMPLETED_RE.search(text)
    topology = _TOPOLOGY_RE.search(text)
    scope = _scope_rows(conn)
    return SnapshotHeader(
        captured_at=parse_ts(captured.group(1)) if captured else None,
        topology=topology.group(1) if topology else (INVENTORY_TOPOLOGY if scope else None),
        scope_systems=tuple(sorted(scope)),
    )


def _data_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        name
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name != ? ORDER BY name",
            (SCOPE_TABLE,),
        )
    ]


def _table_role(table: str, topology: str | None) -> str:
    if topology == INVENTORY_TOPOLOGY:
        return "inventory"
    return "primary" if "primary" in table else "replica"


class SqlAdapter:
    def __init__(self, report: IngestReport | None = None) -> None:
        self._report = report

    def parse(self, path: Path, row: ManifestRow) -> list[Fact]:
        conn = load_snapshot(path)
        try:
            header = read_header(path, conn)
            em = Emitter(row, self._report)
            em.check_capture(header.captured_at, "in-file capture time")
            for system, captured in _scope_rows(conn).items():
                em.check_capture(captured, f"snapshot_scope[{system}]")
            facts: list[Fact] = []
            for table in _data_tables(conn):
                facts.extend(self._table_facts(conn, table, header, row))
            return facts
        finally:
            conn.close()

    def _table_facts(
        self, conn: sqlite3.Connection, table: str, header: SnapshotHeader, row: ManifestRow
    ) -> list[Fact]:
        em = Emitter(row, self._report, scope=table, src="db_row")
        fallback = header.captured_at or row.captured_at
        role = _table_role(table, header.topology)
        columns = [c[1] for c in conn.execute(f'PRAGMA table_info("{table}")')]
        cursor = conn.execute(f'SELECT rowid, * FROM "{table}" ORDER BY resource_id, rowid')
        systems_seen: set[str] = set()
        for rowid, *values in cursor:
            record = dict(zip(columns, values, strict=True))
            system = record.get("origin_system") or TABLE_SYSTEM.get(table)
            if system is None:
                em.warn(f"table {table} has no known owning system")
            else:
                systems_seen.add(system)
            fields = {k: v for k, v in record.items() if k not in _TS_COLUMNS}
            extra: dict[str, Any] = {"role": role}
            if "record_json" in record:
                fields = json.loads(record["record_json"])
                extra["collection"] = record.get("collection_name")
                if fields.get("resource_id") not in (None, record["resource_id"]):
                    em.warn(f"{table}.{record['resource_id']}: record_json resource_id differs")
            observed = next((record[c] for c in _TS_COLUMNS if record.get(c)), None)
            fields["resource_id"] = record["resource_id"]
            emit_record(
                em,
                parse_ts(observed) if observed else fallback,
                system_id=system,
                resource_id=record["resource_id"],
                table=table,
                fields=fields,
                topology=header.topology,
                source_line=f"{table}#{rowid}",
                **extra,
            )
        self._coverage_fact(em, conn, table, header, role, systems_seen, fallback)
        return em.facts

    def _coverage_fact(
        self,
        em: Emitter,
        conn: sqlite3.Connection,
        table: str,
        header: SnapshotHeader,
        role: str,
        systems_seen: set[str],
        fallback: datetime,
    ) -> None:
        system = TABLE_SYSTEM.get(table)
        observed = fallback
        if header.topology == INVENTORY_TOPOLOGY:
            scope = _scope_rows(conn)
            if system not in scope:
                return  # out of scope: an empty table proves nothing about this system
            observed = parse_ts(scope[system])
        attrs: dict[str, object] = {ATTR_COVERAGE: "true", "role": role}
        if all(_is_empty(conn, t) for t in _data_tables(conn)):
            attrs[ATTR_VACUOUS] = "true"
        if system is None and systems_seen:
            attrs["systems"] = ",".join(sorted(systems_seen))
        em.emit(
            FactKind.ABSENCE,
            observed,
            system_id=system,
            resource_id=None,
            table=table,
            topology=header.topology,
            attrs=attrs,
        )


def _is_empty(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is None
