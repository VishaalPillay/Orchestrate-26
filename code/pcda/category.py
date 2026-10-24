"""Map collection / table / event / record_type names to the 15 policy categories.

Deterministic first: a static map keyed by (name, signal) where a signal is a
field that changes meaning, e.g. a contact_lookup event carrying email_opt_in
is MARKETING_PROFILE even inside analytics_platform (case_12). Unknown names
go to an LLM fallback whose answer must be one of CATEGORIES (or None) and is
cached per name, so the same name always resolves the same way. In
deterministic mode there is no fallback: unknown names are flagged, never
guessed.

A resource's category is time-invariant (like ownership), so it is resolved
from every fact about the resource, whatever its capture time.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Final, Iterable

from pcda.facts import ATTR_TABLE, Fact
from pcda.lineage import Claim
from pcda.llm.client import LLMClient, RunMode

CATEGORIES: Final[tuple[str, ...]] = (
    "USER_PROFILE",
    "SECONDARY_IDENTIFIER",
    "SEARCH_INDEX_ENTRY",
    "CACHE_ENTRY",
    "SUPPORT_MESSAGE",
    "SUPPORT_ATTACHMENT",
    "ANALYTICS_PROFILE",
    "PRODUCT_TELEMETRY",
    "USER_EXPORT",
    "MARKETING_PROFILE",
    "EMAIL_DELIVERY_LOG",
    "SECURITY_AUDIT_LOG",
    "LEGAL_HOLD_RECORD",
    "BACKUP_ARCHIVE",
    "ACCOUNT_TOMBSTONE",
)

# Not a policy category: flows carry an implicit DISABLED obligation (policy.py).
DATA_FLOW: Final = "DATA_FLOW"

# Source names seen in participant evidence, grouped by category. Collections,
# inventory tables, log events (adapters store the event as the table) and PDF
# titles all land in attrs["table"] or attrs["collection"].
_NAMES: Final[dict[str, tuple[str, ...]]] = {
    "USER_PROFILE": (
        "customer_profiles", "customer_profiles_primary", "customer_profiles_failover_replica",
        "profile_lookups", "profile_lookup_completed",
    ),
    "SEARCH_INDEX_ENTRY": (
        "customer_search_documents", "search_documents_primary", "index_queries", "index_query_completed",
    ),
    "SUPPORT_MESSAGE": (
        "support_conversations", "support_records", "tickets",
        "message_pii_scan", "message_read", "support_ticket_read_completed",
    ),
    "SUPPORT_ATTACHMENT": (
        "ticket_attachments", "attachment_pii_scan", "DELIVERY ADDRESS CORRECTION REQUEST",
    ),
    "ANALYTICS_PROFILE": (
        "customer_analytics_profiles", "customer_analytics_reports", "profiles",
        "analytics_profile_read", "analytics_profile_read_completed", "analytics_identity_scan",
    ),
    "USER_EXPORT": (
        "customer_exports", "customer_export_jobs", "exports",
        "export_download_check_completed", "export_metadata_read_completed",
    ),
    "SECURITY_AUDIT_LOG": (
        "audit_records", "security_audit_events", "security_audit_records", "audit_record_read_completed",
        "COMPLIANCE JOURNAL - SIGNED RECORD EXTRACT",
    ),
    "LEGAL_HOLD_RECORD": (
        "retained_order_records", "retained_order_loaded", "DISPUTED ORDERS AND TAX RECORDS REGISTER",
    ),
    "BACKUP_ARCHIVE": (
        "backup_catalog", "restore_entries", "backup_restore_entries",
        "restore_policy_read_completed", "restore_candidate_evaluated", "restore_authorization_completed",
    ),
    "ACCOUNT_TOMBSTONE": ("deleted_customer_references",),
    DATA_FLOW: (
        "connectors", "connector_configurations", "connector_subscriptions",
        "connector_config_loaded", "connector_config_read_completed", "connector_heartbeat",
    ),
}

# Resources known only from system_activity (no fact at all, e.g. accounts
# attributed through activity_temporal) resolve through the activity row's
# target system. Keyed "activity:<target_system>".
_ACTIVITY_TARGETS: Final[dict[str, str]] = {
    "account_service": "USER_PROFILE",
    "search_service": "SEARCH_INDEX_ENTRY",
    "analytics_platform": "ANALYTICS_PROFILE",
    "export_service": "USER_EXPORT",
    "integration_service": DATA_FLOW,
    "backup_service": "BACKUP_ARCHIVE",
    "security_service": "SECURITY_AUDIT_LOG",
    "identity_service": "ACCOUNT_TOMBSTONE",
    "cache_service": "CACHE_ENTRY",
    "email_delivery_service": "EMAIL_DELIVERY_LOG",
    "legal_service": "LEGAL_HOLD_RECORD",
    "marketing_platform": "MARKETING_PROFILE",
}

# (name, signal) -> category. signal "" means "no qualifying field".
STATIC_MAP: Final[dict[tuple[str, str], str]] = {
    **{(c, ""): c for c in CATEGORIES},
    (DATA_FLOW, ""): DATA_FLOW,
    **{(name, ""): cat for cat, names in _NAMES.items() for name in names},
    **{(f"activity:{system}", ""): cat for system, cat in _ACTIVITY_TARGETS.items()},
    # case_12: a contact lookup that carries a marketing opt-in is a marketing profile.
    ("contact_lookup_completed", "email_opt_in"): "MARKETING_PROFILE",
}

# Attribute keys that act as signals when present on a fact.
SIGNAL_KEYS: Final[frozenset[str]] = frozenset({"email_opt_in"})


@dataclass(frozen=True)
class CategoryResolution:
    category: str | None
    source: str  # the name that decided, e.g. "rank1:customer_profiles"
    unknown_names: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()  # other categories the resource's names pointed to


class CategoryResolver:
    def __init__(self, client: LLMClient | None) -> None:
        self._client = client
        self._cache: dict[tuple[str, str], str | None] = {}
        self._lock = threading.Lock()

    def resolve(self, name: str, signals: frozenset[str] = frozenset()) -> str | None:
        """Category for a source name, or None if unknown and no fallback is allowed."""
        for signal in sorted(signals):
            hit = STATIC_MAP.get((name, signal))
            if hit is not None:
                return hit
        hit = STATIC_MAP.get((name, ""))
        if hit is not None:
            return hit
        if self._client is None or self._client.mode is RunMode.DETERMINISTIC:
            return None
        key = (name, ",".join(sorted(signals)))
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        answer = self._llm_fallback(name, signals)
        with self._lock:
            self._cache[key] = answer
        return answer

    def resolve_resource(self, facts: Iterable[Fact], claims: Iterable[Claim] = ()) -> CategoryResolution:
        """Category of one resource from all of its facts (any capture time).

        Precedence: an explicit category or record_type label, then the
        logical collection (SQL replica rows keep it apart from the replica
        table), then the table / event / document title, then the target
        system of activity rows when there is no fact at all. Signals are
        pooled per name across the resource's facts.
        """
        pooled: dict[tuple[int, str], frozenset[str]] = {}

        def add(rank: int, name: str, signals: frozenset[str]) -> None:
            pooled[(rank, name)] = pooled.get((rank, name), frozenset()) | signals

        for f in facts:
            if f.is_coverage:
                continue
            signals = frozenset(k for k in f.attrs if k in SIGNAL_KEYS)
            if f.category:
                add(0, f.category, signals)
            if f.attrs.get("record_type"):
                add(0, f.attrs["record_type"], signals)
            if f.attrs.get("collection"):
                add(1, f.attrs["collection"], signals)
            if f.attrs.get(ATTR_TABLE):
                add(2, f.attrs[ATTR_TABLE], signals)
        if not pooled:
            for c in claims:
                add(3, f"activity:{c.target_system}", frozenset())

        decided: str | None = None
        decided_rank = 99
        source = ""
        unknown: list[tuple[int, str]] = []
        seen: set[str] = set()
        for rank, name in sorted(pooled):
            cat = self.resolve(name, pooled[(rank, name)])
            if cat is None:
                unknown.append((rank, name))
                continue
            seen.add(cat)
            if decided is None:
                decided, decided_rank, source = cat, rank, f"rank{rank}:{name}"
        conflicts = tuple(sorted(seen - {decided})) if decided else ()
        # Containers below the deciding rank (replica tables, "ledger", "handover")
        # are expected to be unknown; only names that could have decided are reported.
        reported = tuple(name for rank, name in unknown if rank <= decided_rank)
        return CategoryResolution(decided, source, reported, conflicts)

    def _llm_fallback(self, name: str, signals: frozenset[str]) -> str | None:
        raise NotImplementedError("category.CategoryResolver._llm_fallback")
