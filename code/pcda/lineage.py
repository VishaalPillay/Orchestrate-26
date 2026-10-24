"""Lineage from system_activity.csv.

Every activity row is a Claim: what a worker said it did, never the state.
The graph answers which flows, syncs and restore paths were active at t and
which resources a flow or backup can reach (recreation risk). It does not
order steps; the planner's canonical key does, and the validator uses
`affects` to prove that order is dependency-safe.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pcda.facts import parse_ts


@dataclass(frozen=True)
class Claim:
    activity_id: str
    event_time: datetime
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


class LineageGraph:
    def __init__(self, claims: list[Claim]) -> None:
        self._claims = sorted(claims, key=lambda c: (c.event_time, c.activity_id))

    @classmethod
    def from_csv(cls, path: Path) -> LineageGraph:
        with path.open(encoding="utf-8", newline="") as fh:
            claims = [
                Claim(
                    activity_id=r["activity_id"],
                    event_time=parse_ts(r["event_time"]),
                    event_type=r["event_type"],
                    source_system=r["source_system"],
                    target_system=r["target_system"],
                    resource_id=r["resource_id"],
                    reported_result=r["reported_result"],
                    details=r["details"],
                )
                for r in csv.DictReader(fh)
            ]
        return cls(claims)

    @property
    def claims(self) -> list[Claim]:
        return list(self._claims)

    def claims_for(self, resource_id: str, until: datetime) -> list[Claim]:
        return [c for c in self._claims if c.resource_id == resource_id and c.event_time <= until]

    def active_flows(self, t: datetime) -> list[str]:
        raise NotImplementedError("lineage.LineageGraph.active_flows")

    def reachable(self, resource_id: str, t: datetime) -> frozenset[str]:
        """Resources that a flow, sync or restore starting at resource_id can write at t."""
        raise NotImplementedError("lineage.LineageGraph.reachable")

    def affects(self, guard_resource_id: str, resource_id: str, t: datetime) -> bool:
        """True if the flow/backup guard_resource_id can copy or recreate resource_id at t."""
        return resource_id in self.reachable(guard_resource_id, t)
