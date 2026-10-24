"""Lineage from system_activity.csv.

Every activity row is a Claim: what a worker said it did, never the state.
The graph answers which flows, syncs and restore paths were active at t and
which resources a flow or backup can reach (recreation risk). It does not
order steps; the planner's canonical key does, and the validator uses
`affects` to prove that order is dependency-safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


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
        raise NotImplementedError("lineage.LineageGraph.from_csv")

    @property
    def claims(self) -> list[Claim]:
        return list(self._claims)

    def claims_for(self, resource_id: str, until: datetime) -> list[Claim]:
        raise NotImplementedError("lineage.LineageGraph.claims_for")

    def active_flows(self, t: datetime) -> list[str]:
        raise NotImplementedError("lineage.LineageGraph.active_flows")

    def reachable(self, resource_id: str, t: datetime) -> frozenset[str]:
        """Resources that a flow, sync or restore starting at resource_id can write at t."""
        raise NotImplementedError("lineage.LineageGraph.reachable")

    def affects(self, guard_resource_id: str, resource_id: str, t: datetime) -> bool:
        """True if the flow/backup guard_resource_id can copy or recreate resource_id at t."""
        return resource_id in self.reachable(guard_resource_id, t)
