"""Map collection / table / event / record_type names to the 15 policy categories.

Deterministic first: a static map keyed by (name, signal) where a signal is a
field that changes meaning, e.g. a contact_lookup event carrying email_opt_in
is MARKETING_PROFILE even inside analytics_platform (case_12). Unknown names
go to an LLM fallback whose answer must be one of CATEGORIES (or None) and is
cached per name, so the same name always resolves the same way.
"""

from __future__ import annotations

import threading
from typing import Final

from pcda.llm.client import LLMClient

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

# (name, signal) -> category. signal "" means "no qualifying field".
# Literal record_type labels map to themselves; the rest is filled in the logic pass.
STATIC_MAP: Final[dict[tuple[str, str], str]] = {
    **{(c, ""): c for c in CATEGORIES},
    (DATA_FLOW, ""): DATA_FLOW,
}


class CategoryResolver:
    def __init__(self, client: LLMClient | None) -> None:
        self._client = client
        self._cache: dict[tuple[str, str], str | None] = {}
        self._lock = threading.Lock()

    def resolve(self, name: str, signals: frozenset[str] = frozenset()) -> str | None:
        """Category for a source name, or None if unknown and no fallback is allowed."""
        raise NotImplementedError("category.CategoryResolver.resolve")

    def _llm_fallback(self, name: str, signals: frozenset[str]) -> str | None:
        raise NotImplementedError("category.CategoryResolver._llm_fallback")
