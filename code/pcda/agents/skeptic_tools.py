"""Read-only tools for the skeptic agent.

Every tool is a view over what the pipeline already holds: the FactStore, the
LineageGraph, the IdentityClosure, the compiled policy and the first-pass
results. A tool returns compact JSON capped at TOOL_CAP characters (Groq's
free tier allows 8K tokens a minute, and the whole history is resent on every
turn). Identifiers are shown as owners ("subject", "user_12", or counted as
unresolved), never as raw emails, phones or hashes. No tool writes anything.

Tool contract. The schemas sent to the model (TOOL_SCHEMAS, SUBMIT_TOOL) are part
of the skeptic's cache key, so their text is frozen; this table is the full
description. Every parameter is a JSON string. Every error comes back as
{"error": ...}: not_found, bad_arguments, missing_argument:<name>,
unknown_tool, or not_an_identifier.

  case_context(case_id)
    Returns: request dates, reported_status, verdict, the case's resources
    (tier, up to 3 evidence ids, first reason cut to 140 characters, category,
    obligation status and state) and at most 8 flags of 90 characters.
    Use first, to see what was decided and why. Its evidence ids count as seen.
  find_facts(identifier)
    identifier: an email, phone, 64-hex hash or user_N, normalized in code.
    Returns the owner of that identifier, the fact count and the last 10 facts.
    A resource id is refused with a hint to use resource_timeline.
    Use to see what else one identifier touches.
  resource_timeline(resource_id)
    Returns the owners resolved from all facts about the resource, its last 6
    non-coverage facts, up to 3 facts that name it as their source
    (pointed_to_by) and its last 6 activity rows, each with after_audit.
    Use first for any pick: it shows whose record this is.
  activity_for(resource_id, from?, to?)
    from/to: optional ISO-8601 instants (unparseable values are ignored).
    Returns up to 10 system_activity rows for the resource in that window.
    Use to see a resource's deletion and sync history.
  candidate_resources(case_id, system_id)
    Returns up to 8 deletion_worker DELETE_RECORD/ANONYMIZE_RECORD rows on the
    system between the case's requested_at and audit_at, nearest to
    reported_at first, each with gap_min, within_window (15 min),
    result_fits_status, owners_from_facts and up to 3 cases holding the
    resource; total counts all rows.
    Use to test an activity pick against its competitors and other requests.
  get_rule(category)
    Returns the deletion rule (condition, action, required_state), or
    not_found with the list of categories.
  submit_verdict(verdict, reason, kind?, resource_id?, fact_id?, other_case_id?)
    verdict in CONFIRM, CHALLENGE, PROPOSE; kind in OTHER_SUBJECT,
    OTHER_REQUEST, MISSED_LINK. Ends the investigation; the gate
    (skeptic_gate.evaluate) checks it. A rejection comes back as this tool's
    result, at most twice.

Limits: every result is cut to TOOL_CAP characters by compact() (trailing list
items first, with a truncated count); at most MAX_CALLS_PER_TARGET turns per
target; the split budget comes from config.skeptic_budgets().
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from pcda.cases import CaseRequest
from pcda.facts import ATTR_LINKED, ATTR_STATE, ATTR_TABLE, Fact, FactStore
from pcda.identity import IdentityClosure, normalize_identifier
from pcda.lineage import Claim, LineageGraph, SystemVote
from pcda.policy import ObligationTemplate

if TYPE_CHECKING:
    from pcda.pipeline import CaseResult

TOOL_CAP: Final = 1500
RESOURCE_ID: Final = re.compile(r"^[a-z][a-z_]*\d+$")
_IGNORED_ATTRS: Final = frozenset({"source_line", "src", "table", "coverage", "is_vacuous", "id_fields"})


def _ts(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%MZ")


def mentions(text: str, resource_id: str) -> bool:
    """resource_id appears in text as a whole token (account_12 is not in account_123)."""
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(resource_id)}(?![0-9])", text) is not None


def fact_mentions(fact: Fact, resource_id: str) -> bool:
    """The fact is about resource_id: its own id, the record it was derived from, or a verbatim co-mention."""
    if fact.resource_id == resource_id or fact.attrs.get(ATTR_LINKED) == resource_id:
        return True
    return any(mentions(v, resource_id) for k, v in fact.attrs.items() if k not in _IGNORED_ATTRS)


@dataclass
class SkepticWorld:
    """Everything the tools and the gate may read. Built once per run."""

    store: FactStore  # the full store: ownership is time-invariant
    lineage: LineageGraph
    closure: IdentityClosure
    templates: dict[str, ObligationTemplate]
    cases: dict[str, CaseRequest]
    results: dict[str, CaseResult]  # first pass, no skeptic, every case of both splits
    canonical: dict[str, SystemVote]
    owners_by_resource: dict[str, set[str]] = field(default_factory=dict)
    picks_by_resource: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        owners: dict[str, set[str]] = defaultdict(set)
        for f in self.store:
            if f.resource_id is None:
                continue
            target = f.attrs[ATTR_LINKED] if f.attrs.get("via") == "source_resource_id" else f.resource_id
            for ident in f.identifiers:
                user = self.closure.subject_of(ident)
                if user is not None:
                    owners[target].add(user)
        self.owners_by_resource = dict(owners)
        picks: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for cid, res in sorted(self.results.items()):
            for a in res.attributions:
                picks[a.resource_id].append((cid, a.tier.name))
        self.picks_by_resource = dict(picks)

    def subject_of(self, case_id: str) -> str | None:
        res = self.results.get(case_id)
        return res.certificate.subject if res else None


class SkepticTools:
    """The six tools, bound to the case under review (for the subject label and audit_at)."""

    def __init__(self, world: SkepticWorld, case_id: str) -> None:
        self.world = world
        self.case = world.cases[case_id]
        self.subject = world.subject_of(case_id)
        self.seen: set[str] = set()  # every fact_id / activity_id a tool has shown

    # ---- helpers -----------------------------------------------------------------

    def _owners(self, identifiers: frozenset[str] | set[str]) -> dict[str, Any]:
        labels: set[str] = set()
        unresolved = 0
        for ident in identifiers:
            user = self.world.closure.subject_of(ident)
            if user is None:
                unresolved += 1
            else:
                labels.add("subject" if user == self.subject else user)
        out: dict[str, Any] = {"owners": sorted(labels)}
        if unresolved:
            out["unresolved"] = unresolved
        return out

    def _late(self, f: Fact) -> bool:
        return f.observed_at > self.case.audit_at or f.captured_at > self.case.audit_at

    def _fact_view(self, f: Fact) -> dict[str, Any]:
        self.seen.add(f.fact_id)
        view: dict[str, Any] = {
            "fact_id": f.fact_id,
            "evidence_id": f.evidence_id,
            "resource_id": f.resource_id,
            "system_id": f.system_id,
            "kind": f.kind.value,
            "observed_at": _ts(f.observed_at),
            "after_audit": self._late(f),
            "table": f.attrs.get(ATTR_TABLE, ""),
        }
        if ATTR_STATE in f.attrs:
            view["state"] = f.attrs[ATTR_STATE]
        if ATTR_LINKED in f.attrs:
            view["linked_resource"] = f.attrs[ATTR_LINKED]
        view.update(self._owners(f.identifiers))
        return view

    def _claim_view(self, c: Claim) -> dict[str, Any]:
        self.seen.add(c.activity_id)
        return {
            "activity_id": c.activity_id,
            "at": _ts(c.event_time),
            "after_audit": c.event_time > self.case.audit_at,
            "event": c.event_type,
            "route": f"{c.source_system}->{c.target_system}",
            "resource_id": c.resource_id,
            "result": c.reported_result,
        }

    # ---- the tools ---------------------------------------------------------------

    def case_context(self, case_id: str) -> dict[str, Any]:
        res = self.world.results.get(case_id)
        case = self.world.cases.get(case_id)
        if res is None or case is None:
            return {"error": "not_found"}
        cert = res.certificate
        picks = [
            {
                "resource_id": o.resource_id,
                "system_id": o.system_id,
                "tier": o.resource.tier.name,
                "evidence": list(o.resource.evidence_ids)[:3],
                "why": (o.resource.reasons[0][:140] if o.resource.reasons else ""),
                "category": o.category,
                "status": o.status.value,
                "state": o.observed_state,
            }
            for o in cert.obligations
        ]
        for p in picks:
            self.seen.update(p["evidence"])
        return {
            "case_id": case_id,
            "subject": "subject" if case_id == self.case.case_id else (cert.subject or "unresolved"),
            "reported_status": case.reported_status,
            "requested_at": _ts(case.requested_at),
            "reported_at": _ts(case.reported_at),
            "audit_at": _ts(case.audit_at),
            "verdict": cert.verdict.value,
            "resources": picks,
            "flags": [f[:90] for f in cert.flags if not f.startswith(("category_llm", "system_canonical"))][:8],
        }

    def find_facts(self, identifier: str) -> dict[str, Any]:
        ident = normalize_identifier(identifier)
        if ident is None:
            hint = "use resource_timeline for resource ids" if RESOURCE_ID.match(identifier.strip()) else "not_an_identifier"
            return {"error": hint}
        facts = self.world.store.by_identifier(ident)
        return {
            "owner": self._owners({ident}),
            "count": len(facts),
            "facts": [self._fact_view(f) for f in facts[-10:]],
        }

    def resource_timeline(self, resource_id: str) -> dict[str, Any]:
        facts = [f for f in self.world.store.by_resource(resource_id) if not f.is_coverage]
        linked = [
            f for f in self.world.store if f.attrs.get(ATTR_LINKED) == resource_id and f.resource_id != resource_id
        ]
        claims = [c for c in self.world.lineage.claims if c.resource_id == resource_id]
        if not (facts or linked or claims):
            return {"error": "not_found"}
        return {
            "resource_id": resource_id,
            "owners_from_facts": sorted(
                "subject" if u == self.subject else u for u in self.world.owners_by_resource.get(resource_id, set())
            ),
            "facts": [self._fact_view(f) for f in facts[-6:]],
            "pointed_to_by": [self._fact_view(f) for f in linked[-3:]],
            "activity": [self._claim_view(c) for c in claims[-6:]],
        }

    def activity_for(self, resource_id: str, from_time: str = "", to_time: str = "") -> dict[str, Any]:
        lo, hi = _parse(from_time), _parse(to_time)
        rows = [
            c
            for c in self.world.lineage.claims
            if c.resource_id == resource_id and (lo is None or c.event_time >= lo) and (hi is None or c.event_time <= hi)
        ]
        if not rows:
            return {"error": "not_found"}
        return {"resource_id": resource_id, "activity": [self._claim_view(c) for c in rows[:10]]}

    def candidate_resources(self, case_id: str, system_id: str) -> dict[str, Any]:
        case = self.world.cases.get(case_id)
        if case is None:
            return {"error": "not_found"}
        from pcda.identity import ACTIVITY_WINDOW, REPORTED_STATUS_RESULT  # late: identity imports lineage

        want = REPORTED_STATUS_RESULT.get(case.reported_status)
        rows = [
            c
            for c in self.world.lineage.claims
            if c.source_system == "deletion_worker"
            and c.event_type in ("DELETE_RECORD", "ANONYMIZE_RECORD")
            and c.target_system == system_id
            and case.requested_at <= c.event_time <= case.audit_at
        ]
        rows.sort(key=lambda c: (abs((c.event_time - case.reported_at).total_seconds()), c.activity_id))
        out = []
        for c in rows[:8]:
            gap = (c.event_time - case.reported_at).total_seconds() / 60
            subject = self.world.subject_of(case_id)
            owners = sorted(
                "subject" if u == subject else u for u in self.world.owners_by_resource.get(c.resource_id, set())
            )
            row = self._claim_view(c)
            row.update(
                {
                    "gap_min": round(gap, 1),
                    "within_window": abs(gap) <= ACTIVITY_WINDOW.total_seconds() / 60,
                    "result_fits_status": c.reported_result == want,
                    "owners_from_facts": owners,
                    "attributed_to": [f"{cid}:{tier}" for cid, tier in self.world.picks_by_resource.get(c.resource_id, [])][:3],
                }
            )
            out.append(row)
        return {"case_id": case_id, "system_id": system_id, "reported_status": case.reported_status, "rows": out, "total": len(rows)}

    def get_rule(self, category: str) -> dict[str, Any]:
        t = self.world.templates.get(category.strip().upper())
        if t is None:
            return {"error": "not_found", "categories": sorted(self.world.templates)}
        return {"category": t.category, "condition": t.condition, "action": t.action, "required_state": t.required_state}

    # ---- dispatch ----------------------------------------------------------------

    def call(self, name: str, arguments: str) -> str:
        try:
            args = json.loads(arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
        except ValueError:
            return compact({"error": "bad_arguments"})
        handlers = {
            "case_context": lambda a: self.case_context(str(a["case_id"])),
            "find_facts": lambda a: self.find_facts(str(a["identifier"])),
            "resource_timeline": lambda a: self.resource_timeline(str(a["resource_id"])),
            "activity_for": lambda a: self.activity_for(str(a["resource_id"]), str(a.get("from", "")), str(a.get("to", ""))),
            "candidate_resources": lambda a: self.candidate_resources(str(a["case_id"]), str(a["system_id"])),
            "get_rule": lambda a: self.get_rule(str(a["category"])),
        }
        handler = handlers.get(name)
        if handler is None:
            return compact({"error": "unknown_tool", "tools": sorted(handlers)})
        try:
            return compact(handler(args))
        except KeyError as exc:
            return compact({"error": f"missing_argument:{exc.args[0]}"})


def _parse(value: str) -> datetime | None:
    if not value:
        return None
    from pcda.facts import parse_ts

    try:
        return parse_ts(value)
    except ValueError:
        return None


def compact(obj: dict[str, Any], cap: int = TOOL_CAP) -> str:
    """Canonical JSON no longer than cap: trailing list items are dropped, with a `truncated` count."""
    dropped = 0
    while True:
        text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
        if len(text) <= cap:
            return text
        biggest: list[Any] | None = None
        for v in obj.values():
            if isinstance(v, list) and v and (biggest is None or len(json.dumps(v)) > len(json.dumps(biggest))):
                biggest = v
        if biggest is None:
            return text[:cap]
        biggest.pop()
        dropped += 1
        obj["truncated"] = dropped


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_S: Final = {"type": "string"}

SUBMIT_TOOL: Final[dict[str, Any]] = _tool(
    "submit_verdict",
    "Finish the review. Call this exactly once, when you have decided.",
    {
        "verdict": {"type": "string", "enum": ["CONFIRM", "CHALLENGE", "PROPOSE"]},
        "kind": {"type": "string", "enum": ["OTHER_SUBJECT", "OTHER_REQUEST", "MISSED_LINK"]},
        "resource_id": _S,
        "fact_id": _S,
        "other_case_id": _S,
        "reason": _S,
    },
    ["verdict", "reason"],
)

TOOL_SCHEMAS: Final[list[dict[str, Any]]] = [
    _tool(
        "case_context",
        "A case's request dates, verdict, resources with tier/evidence/category/status, and flags.",
        {"case_id": _S},
        ["case_id"],
    ),
    _tool(
        "find_facts",
        "Evidence facts that carry an email, phone, hash or user_id. Shows owners, never the raw identifier.",
        {"identifier": _S},
        ["identifier"],
    ),
    _tool(
        "resource_timeline",
        "Facts and activity rows about one resource id (account_123, search_45, ...), with who owns each fact.",
        {"resource_id": _S},
        ["resource_id"],
    ),
    _tool(
        "activity_for",
        "system_activity rows for one resource id, optionally inside an ISO-8601 time window.",
        {"resource_id": _S, "from": _S, "to": _S},
        ["resource_id"],
    ),
    _tool(
        "candidate_resources",
        "deletion_worker rows on a system between a case's requested_at and audit_at, nearest to its reported_at "
        "first, with the gap in minutes, whether the result fits the reported status, fact owners and which cases hold the resource.",
        {"case_id": _S, "system_id": _S},
        ["case_id", "system_id"],
    ),
    _tool(
        "get_rule",
        "The deletion rule for a data category (condition, action, required state).",
        {"category": _S},
        ["category"],
    ),
]
