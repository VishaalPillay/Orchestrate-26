"""Lineage from system_activity.csv.

Every activity row is a Claim: what a worker said it did, never the state.
The graph answers which flows, syncs and restore paths were active at t and
which resources a flow or backup can reach (recreation risk). It does not
order steps; the planner's canonical key does, and the validator uses
`affects` to prove that order is dependency-safe.
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

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

    def claim(self, activity_id: str) -> Claim | None:
        return next((c for c in self._claims if c.activity_id == activity_id), None)

    def claims_for(self, resource_id: str, until: datetime) -> list[Claim]:
        return [c for c in self._claims if c.resource_id == resource_id and c.event_time <= until]

    def active_flows(self, t: datetime) -> list[str]:
        """Flows whose latest CHECK/DISABLE claim at or before t is a CHECK (claimed enabled).

        A claim, not state: the solver reads flow state from facts only.
        """
        last: dict[str, Claim] = {}
        for c in self._claims:
            if c.event_time <= t and c.event_type in ("CHECK_DATA_FLOW", "DISABLE_DATA_FLOW"):
                last[c.resource_id] = c
        return sorted(rid for rid, c in last.items() if c.event_type == "CHECK_DATA_FLOW")

    def reachable(self, resource_id: str, t: datetime) -> frozenset[str]:
        """Resources that a flow, sync or restore starting at resource_id can write at t.

        Only restores name their downstream resource ("Restore job repopulated
        downstream resource search_4882."); flow and sync rows name a single
        resource each, so they add no edge.
        """
        out: set[str] = set()
        for c in self._claims:
            if c.resource_id == resource_id and c.event_type == "RESTORE_BACKUP" and c.event_time <= t:
                out.update(_DOWNSTREAM.findall(c.details))
        return frozenset(out)

    def canonical_systems(self) -> dict[str, SystemVote]:
        """resource-id prefix -> the system that owns resources with that prefix.

        Learned from activity rows, by tier: data_catalog LINK_RESOURCE_IDENTITY
        rows name the owning system outright; deletion_worker policy actions
        target where the record lives; any other row is a last resort. Raw row
        counts would be wrong for three prefixes, because transport events
        (RETAIN_RECORD into archive_service, CREATE_EXPORT into object_store,
        search UPDATE_RECORD of support_ ids) outnumber the owning system.
        The majority target within the best non-empty tier wins; ties go to
        the lexicographically smallest system.
        """
        votes: dict[str, list[Counter[str]]] = defaultdict(lambda: [Counter(), Counter(), Counter()])
        for c in self._claims:
            prefix = resource_prefix(c.resource_id)
            if c.event_type == "LINK_RESOURCE_IDENTITY":
                tier = 0
            elif c.source_system == "deletion_worker" and c.event_type in _POLICY_ACTIONS:
                tier = 1
            else:
                tier = 2
            votes[prefix][tier][c.target_system] += 1
        out: dict[str, SystemVote] = {}
        for prefix, tiers in sorted(votes.items()):
            for tier, counter in enumerate(tiers):
                if counter:
                    system = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                    all_targets: Counter[str] = Counter()
                    for c2 in tiers:
                        all_targets.update(c2)
                    out[prefix] = SystemVote(prefix, system, _TIER_NAMES[tier], dict(counter), dict(all_targets))
                    break
        return out

    def non_deletion_classifications(self, until: datetime) -> list[Claim]:
        """CLASSIFY_REQUEST rows that say intake found no deletion request."""
        return [
            c
            for c in self._claims
            if c.event_type == "CLASSIFY_REQUEST" and c.event_time <= until and "non-deletion" in c.details
        ]


_DOWNSTREAM: Final = re.compile(r"downstream resource ([a-z_]+\d+)")
_POLICY_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "DELETE_RECORD",
        "ANONYMIZE_RECORD",
        "REDACT_RECORD",
        "RETAIN_RECORD",
        "DISABLE_DATA_FLOW",
        "BLOCK_RESTORE",
        "CHECK_DATA_FLOW",
        "CHECK_RESTORE_ACCESS",
    }
)
_TIER_NAMES: Final = ("catalog", "policy_action", "any_activity")


def resource_prefix(resource_id: str) -> str:
    """'audit_record_4902' -> 'audit_record_'. The id itself is never inferred."""
    return re.sub(r"\d+$", "", resource_id)


@dataclass(frozen=True)
class SystemVote:
    prefix: str
    system_id: str
    tier: str  # which evidence tier decided
    tier_votes: dict[str, int]
    all_targets: dict[str, int]  # every target_system seen for the prefix, any tier

    def affects(self, guard_resource_id: str, resource_id: str, t: datetime) -> bool:
        """True if the flow/backup guard_resource_id can copy or recreate resource_id at t."""
        return resource_id in self.reachable(guard_resource_id, t)
