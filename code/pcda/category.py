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

import json
import os
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

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

# Names whose category is never stated anywhere and was inferred by reading the
# evidence (plus, where noted, the sample gold that fixed the rule's action).
# Kept apart so a later run can remove them and test whether the LLM category
# fallback recovers them on its own.
INFERRED_FROM_EVIDENCE: Final[dict[tuple[str, str], tuple[str, str]]] = {
    # case_12, evidence_01: "event=contact_lookup_completed ... email_opt_in=true" in
    # analytics_platform. The marketing console screen shows "Email opt-in" as a
    # marketing attribute; gold deletes analytics_9914 (ANALYTICS_PROFILE would anonymize).
    ("contact_lookup_completed", "email_opt_in"): (
        "MARKETING_PROFILE",
        "contact lookup carrying a marketing opt-in; gold case_12 DELETE",
    ),
    # case_10, evidence_61 (support_replica.log, the only file with these events): the
    # batch reads source=legacy_contact_index; contact_query_started looks a contact up
    # by email (lookup_key=<old yahoo address>) and contact_query_completed returns
    # resource_id=support_002 rows=1. The record maps an email to a support record: an
    # identifier entry, not a conversation (SUPPORT_MESSAGE would REDACT) and not a file
    # (SUPPORT_ATTACHMENT). Gold case_10 deletes support_002, matching rule_02
    # SECONDARY_IDENTIFIER (Always, DELETE).
    ("contact_query_completed", ""): (
        "SECONDARY_IDENTIFIER",
        "legacy contact-index entry keyed by email; gold case_10 DELETE",
    ),
    # Same file: deletion_marker_check resource_id=support_002 marker_found=false
    # last_write=2026-09-07 is the replica checking that same index entry for a
    # deletion marker; it names the same record, so the same category.
    ("deletion_marker_check", ""): (
        "SECONDARY_IDENTIFIER",
        "deletion-marker probe of the contact-index entry; gold case_10 DELETE",
    ),
}

# (name, signal) -> category. signal "" means "no qualifying field".
STATIC_MAP: Final[dict[tuple[str, str], str]] = {
    **{(c, ""): c for c in CATEGORIES},
    (DATA_FLOW, ""): DATA_FLOW,
    **{(name, ""): cat for cat, names in _NAMES.items() for name in names},
    **{(f"activity:{system}", ""): cat for system, cat in _ACTIVITY_TARGETS.items()},
    **{key: cat for key, (cat, _) in INFERRED_FROM_EVIDENCE.items()},
}

# Experiment switch (evaluation only): PCDA_DISABLE_INFERRED=1 hides the
# INFERRED_FROM_EVIDENCE entries, to test whether the LLM fallback recovers them.
_DISABLE_INFERRED_ENV: Final = "PCDA_DISABLE_INFERRED"

CATEGORY_TASK: Final = "category.fallback"
CATEGORY_MODEL_ENV: Final = "CATEGORY_MODEL"
UNKNOWN: Final = "UNKNOWN"
ALLOWED: Final[tuple[str, ...]] = CATEGORIES + (DATA_FLOW,)
_MASKS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"\b[0-9a-f]{64}\b"), "<hash>"),
    (re.compile(r"\buser_\d+\b"), "<user_id>"),
    (re.compile(r"\+?\d[\d\s().-]{6,}\d"), "<phone>"),
)


def mask(value: str) -> str:
    for rx, token in _MASKS:
        value = rx.sub(token, value)
    return value


def _static_lookup(name: str, signal: str) -> str | None:
    if os.environ.get(_DISABLE_INFERRED_ENV) == "1" and (name, signal) in INFERRED_FROM_EVIDENCE:
        return None
    return STATIC_MAP.get((name, signal))

# Attribute keys that act as signals when present on a fact.
SIGNAL_KEYS: Final[frozenset[str]] = frozenset({"email_opt_in"})


@dataclass(frozen=True)
class CategoryResolution:
    category: str | None
    source: str  # the name that decided, e.g. "rank1:customer_profiles"
    unknown_names: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()  # other categories the resource's names pointed to


class CategoryResolver:
    def __init__(self, client: LLMClient | None, rule_text: dict[str, str] | None = None) -> None:
        self._client = client
        self._rule_text = rule_text or {}
        self._cache: dict[tuple[str, str], str | None] = {}
        self._lock = threading.Lock()
        self.llm_answers: dict[tuple[str, str], tuple[str, str]] = {}  # key -> (answer, rationale)

    def resolve(self, name: str, signals: frozenset[str] = frozenset()) -> str | None:
        """Static category for a source name, or None. Never calls a model."""
        for signal in sorted(signals):
            hit = _static_lookup(name, signal)
            if hit is not None:
                return hit
        return _static_lookup(name, "")

    def resolve_llm(self, name: str, signals: frozenset[str], examples: list[dict[str, str]]) -> str | None:
        """LLM fallback for a name the static map does not cover; cached per (name, signals)."""
        if self._client is None or self._client.mode is RunMode.DETERMINISTIC:
            return None
        key = (name, ",".join(sorted(signals)))
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        answer = self._llm_fallback(name, signals, examples)
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
        facts_list = list(facts)

        def add(rank: int, name: str, signals: frozenset[str]) -> None:
            pooled[(rank, name)] = pooled.get((rank, name), frozenset()) | signals

        for f in facts_list:
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
        # Activity target systems decide only when no fact name does (e.g. a
        # resource seen only on a screenshot, whose screen title is not a category).
        fact_decides = any(self.resolve(name, sig) is not None for (_, name), sig in pooled.items())
        if not fact_decides:
            for c in claims:
                add(3, f"activity:{c.target_system}", frozenset())

        decided: str | None = None
        decided_rank = 99
        source = ""
        unknown: list[tuple[int, str]] = []
        seen: set[str] = set()
        ordered = sorted(pooled)
        fact_names = [(r, n) for r, n in ordered if r < 3]
        activity_names = [(r, n) for r, n in ordered if r == 3]
        for rank, name in fact_names:
            cat = self.resolve(name, pooled[(rank, name)])
            if cat is None:
                unknown.append((rank, name))
                continue
            seen.add(cat)
            if decided is None:
                decided, decided_rank, source = cat, rank, f"rank{rank}:{name}"
        if decided is None and unknown:
            # Static map silent for every fact name: ask the LLM (model modes only),
            # in precedence order, before falling back to activity target systems.
            by_name = _examples_by_name(facts_list)
            for rank, name in unknown:
                cat = self.resolve_llm(name, pooled[(rank, name)], by_name.get(name, []))
                if cat is not None:
                    seen.add(cat)
                    decided, decided_rank, source = cat, rank, f"llm:rank{rank}:{name}"
                    break
        for rank, name in activity_names:
            cat = self.resolve(name, pooled[(rank, name)])
            if cat is None:
                unknown.append((rank, name))
                continue
            if decided is None:
                seen.add(cat)
                decided, decided_rank, source = cat, rank, f"rank{rank}:{name}"
        if seen == _SUPPORT_PAIR:
            # Name order is arbitrary here; the record content decides (iteration 19).
            decided, source = support_by_content(facts_list)
        conflicts = tuple(sorted(seen - {decided})) if decided else ()
        # Containers below the deciding rank (replica tables, "ledger", "handover")
        # are expected to be unknown; only names that could have decided are reported.
        reported = tuple(name for rank, name in unknown if rank <= decided_rank)
        return CategoryResolution(decided, source, reported, conflicts)

    def _llm_fallback(self, name: str, signals: frozenset[str], examples: list[dict[str, str]]) -> str | None:
        from pcda.agents import prompts
        from pcda.llm.client import LLMRequest, LLMUnavailable

        system, user = prompts.load(CATEGORY_TASK)
        options = "\n".join(
            f"- {c}: {self._rule_text.get(c, 'not a policy category: a sync or integration flow, must be disabled')}"
            for c in ALLOWED
        )
        payload = {
            "name": name,
            "attribute_keys": sorted({k for ex in examples for k in ex}),
            "signals": sorted(signals),
            "example_records": examples[:3],
        }
        prompt = user.replace("{{CATEGORIES}}", options).replace(
            "{{SOURCE}}", json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
        )
        request = LLMRequest(task=CATEGORY_TASK, prompt=prompt, system=system, model_env=CATEGORY_MODEL_ENV,
                             schema={"type": "object", "required": ["category", "rationale"]})
        try:
            resp = self._client.complete(request)  # type: ignore[union-attr]
        except LLMUnavailable:
            return None
        answer = str(resp.parsed.get("category", "")).strip().upper() if isinstance(resp.parsed, dict) else ""
        rationale = str(resp.parsed.get("rationale", ""))[:300] if isinstance(resp.parsed, dict) else ""
        self.llm_answers[(name, ",".join(sorted(signals)))] = (answer, rationale)
        return answer if answer in ALLOWED else None  # UNKNOWN or off-list -> no category


_SUPPORT_PAIR: Final = frozenset({"SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT"})
# A file: what rule_06 ("attachment belongs only to the deletion subject") deletes.
_FILE_KEYS: Final = frozenset({
    "file", "file_name", "filename", "content_type", "mime_type", "size", "size_bytes",
    "storage_path", "attachment", "document",
})
# Another party's context: what rule_05 ("shared customer, agent, or order context") retains.
_SHARED_KEYS: Final = frozenset({"agent", "agent_id", "assigned_agent", "order", "order_id", "order_reference"})
_TEXT_KEYS: Final = ("message_text", "message_excerpt", "body", "conversation")
_SHARED_TEXT: Final = re.compile(r"\border\b|\bORD-\d+|\bagent\b", re.I)


def support_by_content(facts: list[Fact]) -> tuple[str, str]:
    """(category, source) for a support record whose names point to both message and attachment.

    Shared context (an agent or order field, or conversation text naming an
    order or agent) means rule_05: SUPPORT_MESSAGE, redact. Otherwise file
    attributes mean rule_06: SUPPORT_ATTACHMENT, delete. Neither signal:
    SUPPORT_MESSAGE, because deleting a possibly shared conversation cannot be
    undone and redacting can.
    """
    records = [f.attrs for f in facts if not f.is_coverage]
    shared = sorted({k for a in records for k in a if k in _SHARED_KEYS})
    shared += [k for a in records for k in _TEXT_KEYS if _SHARED_TEXT.search(a.get(k, "") or "")]
    if shared:
        return "SUPPORT_MESSAGE", f"content:shared_context:{shared[0]}"
    files = sorted({k for a in records for k in a if k in _FILE_KEYS})
    if files:
        return "SUPPORT_ATTACHMENT", f"content:file_attributes:{','.join(files)}"
    return "SUPPORT_MESSAGE", "content:no_signal"


_EXAMPLE_SKIP: Final =frozenset({"src", "source_line", "id_fields", "pii_fields", "trace_id", "coverage", "is_vacuous"})


def _examples_by_name(facts: list[Fact]) -> dict[str, list[dict[str, str]]]:
    """Up to 3 masked example records per source name, in fact order (deterministic)."""
    out: dict[str, list[dict[str, str]]] = {}
    for f in sorted(facts, key=lambda f: f.fact_id):
        if f.is_coverage:
            continue
        record = {k: mask(v) for k, v in sorted(f.attrs.items()) if k not in _EXAMPLE_SKIP}
        record["kind"] = f.kind.value
        for key in ("record_type", "collection", ATTR_TABLE):
            name = f.attrs.get(key)
            if name and len(out.setdefault(name, [])) < 3 and record not in out[name]:
                out[name].append(record)
    return out
