"""Identity: who the subject is, and which resources are theirs.

Two separate questions, two structures:

  * IdentityClosure (global, case-independent): union-find over identifiers
    only (current snapshot, audit-log history, user_id in DB rows). Exact
    edges only; it never contains resources.
  * ResourceAttribution (per case): candidate resource -> subject links, each
    with a tier, the evidence used and reasons, judged against the case
    window. The case_17 decoy (account_6523, linked by an exact email in a
    March lookup) is rejected here, never in the union-find.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta
from enum import IntEnum
from pathlib import Path
from typing import Final

from pcda.cases import CaseRequest
from pcda.facts import FactStore
from pcda.lineage import LineageGraph

_EMAIL_RE: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HASH_RE: Final = re.compile(r"^[0-9a-f]{64}$")


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str) -> str:
    return re.sub(r"\D", "", value)


def email_hash(email: str) -> str:
    """sha256(email.strip().lower()); recon matched all 850 snapshot and 214 audit hashes."""
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


def normalize_identifier(value: str) -> str | None:
    """Canonical form used as a FactStore index key, or None if not an identifier."""
    v = value.strip()
    if not v:
        return None
    if _EMAIL_RE.match(v):
        return normalize_email(v)
    if _HASH_RE.match(v.lower()):
        return v.lower()
    if v.startswith("user_"):
        return v
    digits = normalize_phone(v)
    if len(digits) >= 7 and re.fullmatch(r"[\d\s+().-]+", v):
        return digits
    return None


class LinkTier(IntEnum):
    """Lower value = stronger evidence that a resource belongs to the subject."""

    DB_ROW = 1
    LEDGER = 2
    CATALOG_LINK = 3
    LOG_TRACE = 4
    ACTIVITY_TEMPORAL = 5


# activity_temporal gate (b): |event_time - reported_at| must be within this.
# Recon: the five accounts found only in system_activity (cases 01, 02, 07,
# 08, 10) sit 0.3-8.0 minutes from reported_at; the nearest wrong candidate in
# the sample is 44 minutes away.
ACTIVITY_WINDOW: Final = timedelta(minutes=15)


class UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        parent = self._parent.setdefault(x, x)
        if parent != x:
            parent = self._parent[x] = self.find(parent)
        return parent

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Deterministic root: lexicographically smallest.
            lo, hi = sorted((ra, rb))
            self._parent[hi] = lo

    def members(self, x: str) -> frozenset[str]:
        root = self.find(x)
        return frozenset(k for k in self._parent if self.find(k) == root)


class IdentityClosure:
    def __init__(self, uf: UnionFind, user_of: dict[str, str]) -> None:
        self._uf = uf
        self._user_of = user_of  # normalized identifier -> user_id

    @classmethod
    def from_csvs(cls, identities: Path, audit_log: Path) -> IdentityClosure:
        raise NotImplementedError("identity.IdentityClosure.from_csvs")

    def subject_of(self, identifier: str) -> str | None:
        raise NotImplementedError("identity.IdentityClosure.subject_of")

    def identifiers_of(self, user_id: str) -> frozenset[str]:
        raise NotImplementedError("identity.IdentityClosure.identifiers_of")


@dataclass(frozen=True)
class AttributedResource:
    resource_id: str
    system_id: str
    tier: LinkTier
    reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    needs_skeptic: bool = False


class ResourceAttribution:
    @staticmethod
    def for_case(
        case: CaseRequest,
        user_id: str,
        closure: IdentityClosure,
        store: FactStore,
        lineage: LineageGraph,
    ) -> list[AttributedResource]:
        """Resources attributable to the subject at case.audit_at (store is already as_of)."""
        raise NotImplementedError("identity.ResourceAttribution.for_case")

    @staticmethod
    def activity_temporal(
        case: CaseRequest,
        lineage: LineageGraph,
        stronger: list[AttributedResource],
    ) -> list[AttributedResource]:
        """Lowest-tier links from system_activity. All four must hold, per system:

        (a) the claim is deletion_worker -> <system> DELETE_RECORD;
        (b) |event_time - case.reported_at| <= ACTIVITY_WINDOW;
        (c) exactly one such candidate for that system in the window;
        (d) no attribution in `stronger` for that system.

        Every result has needs_skeptic=True and cites the activity_id.
        Resource ids are never inferred from naming patterns.
        """
        raise NotImplementedError("identity.ResourceAttribution.activity_temporal")
