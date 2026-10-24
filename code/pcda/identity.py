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

import csv
import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from pathlib import Path
from typing import Final

from pcda.cases import CaseRequest
from pcda.facts import ATTR_LINKED, ATTR_SRC, ATTR_TABLE, Fact, FactKind, FactStore
from pcda.lineage import Claim, LineageGraph

_EMAIL_RE: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HASH_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_EMAIL_IN_TEXT: Final = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_USER_IN_TEXT: Final = re.compile(r"\buser_\d+\b")


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


# Fact origin kind (ATTR_SRC, stamped by the adapters) -> tier.
_SRC_TIER: Final[dict[str, LinkTier]] = {
    "db_row": LinkTier.DB_ROW,
    "ledger": LinkTier.LEDGER,
    "document": LinkTier.LEDGER,
    "log": LinkTier.LOG_TRACE,
}

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

    def groups(self) -> dict[str, frozenset[str]]:
        """root -> members, in one pass."""
        out: dict[str, set[str]] = defaultdict(set)
        for k in list(self._parent):
            out[self.find(k)].add(k)
        return {root: frozenset(m) for root, m in out.items()}


class IdentityClosure:
    def __init__(
        self,
        uf: UnionFind,
        user_of: dict[str, str],
        conflicts: tuple[tuple[str, str, str], ...] = (),
    ) -> None:
        self._uf = uf
        self._user_of = user_of  # normalized identifier -> user_id
        self.conflicts = conflicts  # (identifier, kept user, rejected user); never unioned
        groups = uf.groups()
        self._by_user = {u: groups[uf.find(u)] for u in set(user_of.values())}

    @classmethod
    def from_csvs(cls, identities: Path, audit_log: Path) -> IdentityClosure:
        """Exact edges only: every identifier is unioned with its user_id.

        Sources: the current snapshot (email, phone, email_hash) and every old
        and new value in the audit log. Each email also contributes its
        sha256, so a hash seen in old evidence resolves even when the audit
        log only recorded the email change. An identifier claimed by two
        users is never unioned (conflicts records it); the data has none.
        """
        uf = UnionFind()
        user_of: dict[str, str] = {}
        conflicts: list[tuple[str, str, str]] = []

        def bind(user: str, ident: str | None) -> None:
            if not ident:
                return
            owner = user_of.get(ident)
            if owner is not None and owner != user:
                conflicts.append((ident, owner, user))
                return
            user_of[ident] = user
            uf.union(user, ident)

        def bind_value(user: str, kind: str, raw: str) -> None:
            raw = raw.strip()
            if not raw:
                return
            if kind == "email":
                email = normalize_email(raw)
                bind(user, email)
                bind(user, email_hash(email))
            elif kind == "phone":
                digits = normalize_phone(raw)
                bind(user, digits if len(digits) >= 7 else None)
            elif kind == "email_hash":
                bind(user, raw.lower())

        with identities.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                user = row["user_id"]
                bind(user, user)
                for kind in ("email", "phone", "email_hash"):
                    bind_value(user, kind, row[kind])
        with audit_log.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                user = row["user_id"]
                bind(user, user)
                for raw in (row["old_value"], row["new_value"]):
                    bind_value(user, row["field_name"], raw)
        return cls(uf, user_of, tuple(conflicts))

    def subject_of(self, identifier: str) -> str | None:
        ident = normalize_identifier(identifier)
        return self._user_of.get(ident) if ident is not None else None

    def identifiers_of(self, user_id: str) -> frozenset[str]:
        """Every identifier the user has ever had, plus the user_id itself."""
        return self._by_user.get(user_id, frozenset())


@dataclass(frozen=True)
class AttributedResource:
    resource_id: str
    system_id: str
    tier: LinkTier
    reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...]  # supporting evidence visible at audit_at: citable
    needs_skeptic: bool = False
    # Supporting evidence captured after audit_at. It established ownership
    # only; it must never be cited or used for state.
    late_evidence_ids: tuple[str, ...] = ()


@dataclass
class _Candidate:
    tier: LinkTier = LinkTier.ACTIVITY_TEMPORAL
    systems: Counter[str] = field(default_factory=Counter)  # votes from best-tier facts
    reasons: set[str] = field(default_factory=set)
    evidence: set[str] = field(default_factory=set)
    late_evidence: set[str] = field(default_factory=set)
    latest: datetime | None = None  # newest supporting observation, any tier

    def add(
        self,
        tier: LinkTier,
        system: str | None,
        reason: str,
        evidence: str,
        at: datetime,
        late: bool = False,
    ) -> None:
        if self.latest is None or at > self.latest:
            self.latest = at
        if tier < self.tier:
            self.tier = tier
            self.systems.clear()
        if tier == self.tier and system:
            self.systems[system] += 1
        self.reasons.add(f"{tier.name.lower()}: {reason}" + (" [post-audit]" if late else ""))
        (self.late_evidence if late else self.evidence).add(evidence)


def _text_identifiers(text: str) -> frozenset[str]:
    found = {normalize_email(m) for m in _EMAIL_IN_TEXT.findall(text)}
    found |= set(_USER_IN_TEXT.findall(text))
    return frozenset(found)


class ResourceAttribution:
    @staticmethod
    def for_case(
        case: CaseRequest,
        user_id: str,
        closure: IdentityClosure,
        store: FactStore,
        lineage: LineageGraph,
    ) -> list[AttributedResource]:
        accepted, _ = ResourceAttribution.for_case_detailed(case, user_id, closure, store, lineage)
        return accepted

    @staticmethod
    def for_case_detailed(
        case: CaseRequest,
        user_id: str,
        closure: IdentityClosure,
        store: FactStore,
        lineage: LineageGraph,
    ) -> tuple[list[AttributedResource], list[AttributedResource]]:
        """(accepted, rejected) resources attributable to the subject.

        Ownership/state split. `store` is the FULL FactStore, not as_of:
        ownership (resource <-> subject) is time-invariant, so a link may come
        from evidence captured at any time. State, citations and verdicts are
        the caller's business and stay strictly as_of(audit_at); evidence that
        was captured after audit_at is reported separately in
        late_evidence_ids so it can never be cited.

        Gates (rejected resources carry the reason as their last reason):
          * post-audit guardrail: a resource only counts if it has at least
            one fact observed at or before audit_at (whatever the capture time
            of its file), or one activity row at or before audit_at. Resources
            first seen after the audit were created after it and are never
            pulled in.
          * staleness: a candidate needs one supporting observation at or
            after case.requested_at. A resource seen only in snapshots that
            predate the request describes an earlier life of the identifier
            (the case_17 decoy account_6523 was last seen 162 days before the
            request).

        Every fact that carries one of the subject's identifiers and names a
        resource is a candidate link. The tier comes from where the fact was
        read (db_row > ledger > log_trace); catalog rows in system_activity
        and source_resource_id pointers give catalog_link; whatever is left
        goes through activity_temporal. The system of a resource is the most
        frequent system_id among its best-tier facts.
        """
        idents = closure.identifiers_of(user_id)
        cands: dict[str, _Candidate] = defaultdict(_Candidate)
        seen: set[str] = set()
        for ident in sorted(idents):
            for fact in store.by_identifier(ident):
                if fact.fact_id in seen or fact.resource_id is None:
                    continue
                seen.add(fact.fact_id)
                ResourceAttribution._from_fact(fact, ident, cands, case.audit_at)
        claim_system: dict[str, str] = {}
        for claim in lineage.claims:
            claim_system.setdefault(claim.resource_id, claim.target_system)
        for claim in lineage.claims:
            if claim.event_type == "LINK_RESOURCE_IDENTITY":
                matched = _text_identifiers(claim.details) & idents
                if matched:
                    cands[claim.resource_id].add(
                        LinkTier.CATALOG_LINK,
                        claim.target_system,
                        f"{claim.activity_id} data catalog links {claim.resource_id} to {sorted(matched)[0]}",
                        claim.activity_id,
                        claim.event_time,
                        late=claim.event_time > case.audit_at,
                    )

        # Post-audit guardrail: what existed at or before audit_at. A fact's time is
        # when it was observed, not when its file was captured: a record observed
        # before the audit in a later-delivered snapshot did exist at the audit.
        existed = {
            f.resource_id
            for f in store
            if f.resource_id is not None and f.observed_at <= case.audit_at
        }
        existed |= {c.resource_id for c in lineage.claims if c.event_time <= case.audit_at}
        post_audit_only = {rid for rid in cands if rid not in existed}

        out = [
            AttributedResource(
                resource_id=rid,
                system_id=(
                    sorted(c.systems.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                    if c.systems
                    else claim_system.get(rid, "unknown")
                ),
                tier=c.tier,
                reasons=tuple(sorted(c.reasons)),
                evidence_ids=tuple(sorted(c.evidence)),
                late_evidence_ids=tuple(sorted(c.late_evidence)),
            )
            for rid, c in cands.items()
            if rid not in post_audit_only
            and c.latest is not None
            and c.latest >= case.requested_at
        ]
        rejected = []
        for rid, c in sorted(cands.items()):
            if rid in post_audit_only:
                why = "post-audit: no fact observed and no activity row at or before audit_at"
            elif c.latest is not None and c.latest < case.requested_at:
                why = (
                    f"stale: newest supporting observation {c.latest:%Y-%m-%d} precedes "
                    f"requested_at {case.requested_at:%Y-%m-%d}"
                )
            else:
                continue
            rejected.append(
                AttributedResource(
                    resource_id=rid,
                    system_id=(c.systems.most_common(1)[0][0] if c.systems else claim_system.get(rid, "unknown")),
                    tier=c.tier,
                    reasons=tuple(sorted(c.reasons)) + (why,),
                    evidence_ids=tuple(sorted(c.evidence)),
                    late_evidence_ids=tuple(sorted(c.late_evidence)),
                )
            )
        out.extend(ResourceAttribution.activity_temporal(case, lineage, out))
        out.sort(key=lambda a: (a.system_id, a.resource_id))
        return out, rejected

    @staticmethod
    def _from_fact(fact: Fact, ident: str, cands: dict[str, _Candidate], audit_at: datetime) -> None:
        assert fact.resource_id is not None
        table = fact.attrs.get(ATTR_TABLE, "?")
        late = fact.captured_at > audit_at or fact.observed_at > audit_at
        if fact.kind is FactKind.LINK and fact.attrs.get("via") == "source_resource_id":
            # The record points at the resource it was derived from (search doc -> account).
            target = fact.attrs[ATTR_LINKED]
            cands[target].add(
                LinkTier.CATALOG_LINK,
                None,
                f"{fact.resource_id} ({table}) names {target} as source_resource_id, matched on {ident}",
                fact.evidence_id,
                fact.observed_at,
                late,
            )
            return
        tier = _SRC_TIER.get(fact.attrs.get(ATTR_SRC, ""), LinkTier.LOG_TRACE)
        cands[fact.resource_id].add(
            tier,
            fact.system_id,
            f"{fact.kind.value} in {table} matched on {ident}",
            fact.evidence_id,
            fact.observed_at,
            late,
        )

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
        in_window: dict[str, list[Claim]] = defaultdict(list)
        for claim in lineage.claims:
            if (
                claim.source_system == "deletion_worker"
                and claim.event_type == "DELETE_RECORD"
                and claim.event_time <= case.audit_at
                and abs(claim.event_time - case.reported_at) <= ACTIVITY_WINDOW
            ):
                in_window[claim.target_system].append(claim)
        covered = {a.system_id for a in stronger}
        out: list[AttributedResource] = []
        for system in sorted(in_window):
            claims = in_window[system]
            resources = {c.resource_id for c in claims}
            if len(resources) != 1 or system in covered:
                continue
            (rid,) = resources
            gaps = ", ".join(
                f"{c.activity_id} {abs(c.event_time - case.reported_at).total_seconds() / 60:.1f} min"
                for c in claims
            )
            out.append(
                AttributedResource(
                    resource_id=rid,
                    system_id=system,
                    tier=LinkTier.ACTIVITY_TEMPORAL,
                    reasons=(
                        f"activity_temporal: sole deletion_worker DELETE_RECORD on {system} "
                        f"within {ACTIVITY_WINDOW} of reported_at ({gaps}); no stronger link for {system}",
                    ),
                    evidence_ids=tuple(sorted(c.activity_id for c in claims)),
                    needs_skeptic=True,
                )
            )
        return out
