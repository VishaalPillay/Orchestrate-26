"""The gate: the only code that turns a skeptic answer into a change.

The agent proposes; this module checks the claim against the stores, and
nothing else can alter an attribution. A claim passes only if every check
listed for its kind passes. Anything else is rejected with the reason, and a
cited id that exists in neither the fact store nor the activity stream is
counted as hallucinated. The result is an Adjustment (a pick to remove, or a
resource to add at the lowest tier); the solver then solves the case again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from pcda.agents.skeptic_tools import SkepticWorld, fact_mentions, mentions
from pcda.facts import Fact, FactKind
from pcda.identity import (
    ACTIVITY_WINDOW,
    REPORTED_STATUS_RESULT,
    AttributedResource,
    LinkTier,
    _text_identifiers,
)
from pcda.lineage import Claim, resource_prefix

if TYPE_CHECKING:
    from pcda.agents.skeptic import Answer, Target

# Tiers the skeptic may challenge: the two activity tiers, which are claims, not evidence.
CHALLENGEABLE: Final = (LinkTier.ACTIVITY_TEMPORAL, LinkTier.ACTIVITY_JOINT)
# A tier at or below this is a fact-based link; another request holding one is not contestable.
STRONG: Final = LinkTier.LOG_TRACE


@dataclass(frozen=True)
class GateResult:
    accepted: bool
    action: str | None = None  # REMOVE | ADD
    resource_id: str | None = None
    checks: tuple[str, ...] = ()  # checks passed, in order
    reject_reason: str = ""
    hallucinated: bool = False
    cited: str = ""
    added: AttributedResource | None = field(default=None, compare=False)


def _reject(reason: str, checks: list[str], cited: str, hallucinated: bool = False) -> GateResult:
    return GateResult(False, None, None, tuple(checks), reason, hallucinated, cited)


def evaluate(answer: Answer, target: Target, world: SkepticWorld) -> GateResult:
    cited = answer.fact_id or ""
    if answer.verdict == "CONFIRM":
        return GateResult(False, cited=cited, reject_reason="confirm")
    case = world.cases.get(target.case_id)
    result = world.results.get(target.case_id)
    if case is None or result is None:
        return _reject("unknown_case", [], cited)
    fact = world.store.get(cited) if cited else None
    claim = world.lineage.claim(cited) if cited and fact is None else None
    if cited == "" or (fact is None and claim is None):
        return _reject("cited_id_not_found", [], cited, hallucinated=bool(cited))
    checks = ["cited_id_exists"]
    if answer.verdict == "CHALLENGE":
        return _challenge(answer, target, world, fact, claim, checks)
    if answer.verdict == "PROPOSE":
        return _propose(answer, target, world, fact, checks)
    return _reject("unknown_verdict", checks, cited)


def _picked(target: Target, answer: Answer, world: SkepticWorld) -> AttributedResource | None:
    rid = answer.resource_id or target.resource_id
    for a in world.results[target.case_id].attributions:
        if a.resource_id == rid and a.tier in CHALLENGEABLE:
            return a
    return None


def _challenge(
    answer: Answer, target: Target, world: SkepticWorld, fact: Fact | None, claim: Claim | None, checks: list[str]
) -> GateResult:
    cited = answer.fact_id or ""
    pick = _picked(target, answer, world)
    if pick is None:
        return _reject("not_a_challengeable_pick", checks, cited)
    checks.append("resource_is_activity_pick")
    rid = pick.resource_id
    case = world.cases[target.case_id]
    subject = world.results[target.case_id].certificate.subject
    if answer.kind == "OTHER_SUBJECT":
        if fact is not None:
            if fact.is_coverage or fact.kind is FactKind.ABSENCE:
                return _reject("fact_is_not_a_record", checks, cited)
            if not fact_mentions(fact, rid):
                return _reject("fact_does_not_link_resource", checks, cited)
            idents = set(fact.identifiers)
        else:
            assert claim is not None
            if claim.event_type != "LINK_RESOURCE_IDENTITY" or not mentions(claim.resource_id + " " + claim.details, rid):
                return _reject("activity_row_carries_no_identity_link", checks, cited)
            idents = set(_text_identifiers(claim.details))
        checks.append("cited_row_links_resource")
        users = {u for u in (world.closure.subject_of(i) for i in idents) if u is not None}
        if not users:
            return _reject("link_resolves_to_no_user", checks, cited)
        if subject in users:
            return _reject("link_includes_this_subject", checks, cited)
        checks.append(f"link_resolves_to_other_user:{'+'.join(sorted(users))}")
        return GateResult(True, "REMOVE", rid, tuple(checks), "", False, cited)
    if answer.kind == "OTHER_REQUEST":
        if claim is None or claim.resource_id != rid or claim.event_type not in ("DELETE_RECORD", "ANONYMIZE_RECORD"):
            return _reject("cited_row_is_not_the_picks_delete_row", checks, cited)
        other = world.cases.get(answer.other_case_id or "")
        if other is None or other.case_id == case.case_id:
            return _reject("other_case_not_found", checks, cited, hallucinated=False)
        checks.append("other_request_exists")
        gap_other = abs((claim.event_time - other.reported_at).total_seconds())
        gap_this = abs((claim.event_time - case.reported_at).total_seconds())
        if not (other.requested_at <= claim.event_time <= min(other.reported_at, other.audit_at) + ACTIVITY_WINDOW):
            return _reject("row_outside_other_requests_window", checks, cited)
        if gap_other > ACTIVITY_WINDOW.total_seconds():
            return _reject("row_not_near_other_reported_at", checks, cited)
        if REPORTED_STATUS_RESULT.get(other.reported_status) != claim.reported_result:
            return _reject("result_does_not_fit_other_status", checks, cited)
        if gap_other >= gap_this:
            return _reject("row_not_closer_to_other_request", checks, cited)
        checks.append(f"row_fits_other_request_better:{gap_other / 60:.1f}min<{gap_this / 60:.1f}min")
        theirs = [
            a for a in world.results[other.case_id].attributions if a.system_id == pick.system_id and a.tier <= STRONG
        ]
        if theirs:
            return _reject(f"other_request_holds_{theirs[0].resource_id}_at_{theirs[0].tier.name}", checks, cited)
        checks.append("other_request_has_no_fact_based_link")
        return GateResult(True, "REMOVE", rid, tuple(checks), "", False, cited)
    return _reject("unknown_challenge_kind", checks, cited)


def _propose(answer: Answer, target: Target, world: SkepticWorld, fact: Fact | None, checks: list[str]) -> GateResult:
    cited = answer.fact_id or ""
    rid = (answer.resource_id or "").strip()
    if fact is None:
        return _reject("proposal_cites_no_fact", checks, cited)
    if not rid:
        return _reject("no_resource_id", checks, cited)
    case = world.cases[target.case_id]
    result = world.results[target.case_id]
    subject = result.certificate.subject
    if subject is None:
        return _reject("case_has_no_subject", checks, cited)
    if fact.is_coverage or fact.kind is FactKind.ABSENCE:
        return _reject("fact_is_not_a_record", checks, cited)
    users = {u for u in (world.closure.subject_of(i) for i in fact.identifiers) if u is not None}
    if users != {subject}:
        return _reject(f"fact_identifiers_resolve_to:{'+'.join(sorted(users)) or 'nobody'}", checks, cited)
    checks.append("fact_identifiers_resolve_to_subject")
    if not fact_mentions(fact, rid):
        return _reject("fact_does_not_mention_resource", checks, cited)
    checks.append("fact_mentions_resource")
    if any(a.resource_id == rid for a in result.attributions):
        return _reject("already_attributed", checks, cited)
    checks.append("not_already_attributed")
    existed = any(f.observed_at <= case.audit_at for f in world.store.by_resource(rid)) or any(
        c.event_time <= case.audit_at for c in world.lineage.claims if c.resource_id == rid
    )
    if not existed:
        return _reject("post_audit_resource", checks, cited)
    checks.append("resource_existed_at_audit")
    if fact.observed_at < case.requested_at:
        return _reject("stale_before_request", checks, cited)
    checks.append("observation_not_stale")
    vote = world.canonical.get(resource_prefix(rid))
    if vote is None:
        return _reject("system_unmapped", checks, cited)
    checks.append(f"system_{vote.system_id}")
    late = fact.captured_at > case.audit_at or fact.observed_at > case.audit_at
    added = AttributedResource(
        resource_id=rid,
        system_id=vote.system_id,
        tier=LinkTier.SKEPTIC,
        reasons=(f"skeptic: {fact.fact_id} mentions {rid} and resolves to the subject; {answer.reason[:120]}",),
        evidence_ids=() if late else (fact.evidence_id,),
        late_evidence_ids=(fact.evidence_id,) if late else (),
    )
    return GateResult(True, "ADD", rid, tuple(checks), "", False, cited, added)
