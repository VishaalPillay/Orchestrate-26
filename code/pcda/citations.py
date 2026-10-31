"""Citation gate: every evidence id a step cites must actually mention the step's resource (invariant 1).

A FINDING, and a VERIFICATION in a COMPLETE case, cites evidence. The cited id must be a fact of the resource (or one that
names it), an activity row on it (or whose details name it), an audit-log row of the subject, or a non-vacuous covering
snapshot of the resource's system. `main.py` runs this after contract validation and stops before writing on a violation.
"""

from __future__ import annotations

import csv
from collections import defaultdict

from pcda import paths
from pcda.facts import ATTR_LINKED, ATTR_VACUOUS, Fact, FactStore
from pcda.lineage import LineageGraph
from pcda.solver import Verdict
from pcda.validator import Violation


class CitationChecker:
    def __init__(self, store: FactStore, lineage: LineageGraph, audit_user: dict[str, str]) -> None:
        self._lineage = lineage
        self._audit_user = audit_user
        self._by_evidence: dict[str, list[Fact]] = defaultdict(list)
        for f in store:
            self._by_evidence[f.evidence_id].append(f)

    @classmethod
    def build(cls, store: FactStore, lineage: LineageGraph) -> CitationChecker:
        with paths.USER_AUDIT_LOG.open(encoding="utf-8", newline="") as fh:
            audit_user = {r["audit_id"]: r["user_id"] for r in csv.DictReader(fh)}
        return cls(store, lineage, audit_user)

    def mentions(self, evidence_id: str, resource_id: str, system_id: str, subject: str | None) -> bool:
        claim = self._lineage.claim(evidence_id)
        if claim is not None:
            return claim.resource_id == resource_id or resource_id in claim.details
        if evidence_id in self._audit_user:
            return self._audit_user[evidence_id] == subject
        for f in self._by_evidence.get(evidence_id, []):
            if f.resource_id == resource_id or f.attrs.get(ATTR_LINKED) == resource_id:
                return True
            if f.is_coverage and f.attrs.get(ATTR_VACUOUS) != "true":
                systems = {s for s in f.attrs.get("systems", "").split(",") if s}
                if f.system_id == system_id or system_id in systems:
                    return True
        return False

    def violations(self, case_id: str, verdict: Verdict, subject: str | None, steps: list) -> list[Violation]:
        out: list[Violation] = []
        for s in steps:
            cited = s.phase == "FINDING" or (verdict is Verdict.COMPLETE and s.phase == "VERIFICATION")
            if cited and s.evidence_id and not self.mentions(s.evidence_id, s.resource_id, s.system_id, subject):
                out.append(Violation(case_id, "citation_mentions", f"{s.operation} {s.resource_id} cites {s.evidence_id}, which does not mention it"))
        return out
