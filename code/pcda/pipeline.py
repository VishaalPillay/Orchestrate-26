"""The control flow, in plain code. Global context is built once; each case is
a fixed sequence of stage calls with no framework in between:

  intake -> subject -> attribution (full store) -> canonical system ->
  intake corroboration -> as_of(audit_at) -> solve -> skeptic -> project
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from pcda import paths
from pcda.agents.intake import IntakeDecision, IntakeResult, LLMIntakeAgent, RuleIntakeAgent, decide_intake
from pcda.agents.perception import LLMPerceptionAgent
from pcda.agents.skeptic import Adjustments, NullSkepticAgent, SkepticAgent
from pcda.cases import CaseRequest, load_requests
from pcda.category import CategoryResolver
from pcda.facts import FactStore
from pcda.identity import (
    AttributedResource,
    IdentityClosure,
    JointActivityAttribution,
    LinkTier,
    ResourceAttribution,
    normalize_email,
)
from pcda.ingest.base import IngestReport, read_manifest
from pcda.ingest.registry import build_adapters, ingest_all
from pcda.lineage import LineageGraph, SystemVote, resource_prefix
from pcda.llm.cache import DiskCache
from pcda.llm.client import LLMClient, RunMode, build_client
from pcda.llm.ledger import TokenLedger
from pcda.planner import Step, order_steps, project
from pcda.policy import ObligationTemplate, compile_rules
from pcda.solver import Certificate, ObligationStatus, Verdict, solve

# Activity rows that may link a resource to a request when nothing else does.
# (event_type, target_system, fallback_only, build options). A fallback_only pass
# is used only when the case would otherwise have no provable obligation at all
# (the NOT_APPLICABLE fallback). One-sided analytics stays fallback-only: on the
# samples it made 1 correct pick, while 3 cases with no analytics record (01, 14,
# 31) reached a single candidate and were stopped only because another case
# contested it. A two-sided analytics pass (the row also forced to the request)
# assigned 0 rows on either split, with or without result-matching, and was
# removed (iterations 15 and 16).
JOINT_PASSES: tuple[tuple[str, str, bool], ...] = (
    ("DELETE_RECORD", "account_service", False),
    ("ANONYMIZE_RECORD", "analytics_platform", True),
)
FALLBACK_FLAG = "fallback_no_provable_obligation"


@dataclass
class Context:
    mode: RunMode
    llm: LLMClient
    ledger: TokenLedger
    templates: dict[str, ObligationTemplate]
    closure: IdentityClosure
    lineage: LineageGraph
    store: FactStore
    resolver: CategoryResolver
    rule_intake: RuleIntakeAgent
    llm_intake: LLMIntakeAgent | None  # None in deterministic mode
    skeptic: SkepticAgent
    # What the code-side gate accepted from the skeptic agent, by case_id (run_skeptic fills these).
    skeptic_adjustments: dict[str, Adjustments] = field(default_factory=dict)
    skeptic_outcomes: dict[str, str] = field(default_factory=dict)  # "case_id:resource_id" -> outcome
    # Cases whose split the skeptic did not run on (live/replay): flagged skeptic_skipped:budget.
    skeptic_skipped: frozenset[str] = frozenset()
    canonical: dict[str, SystemVote] = field(default_factory=dict)
    # Activity-derived picks that two or more different subjects would each receive:
    # resource_id -> the case_ids that picked it. A row cannot belong to two people.
    shared_picks: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # One joint pass per (event_type, target_system); see JOINT_PASSES.
    joint: tuple[JointActivityAttribution, ...] = ()
    report: IngestReport = field(default_factory=IngestReport)


@dataclass(frozen=True)
class CaseResult:
    certificate: Certificate
    steps: list[Step]
    intake: IntakeResult
    # CLASSIFY_REQUEST "non-deletion" activity ids on the subject's resources.
    intake_evidence: tuple[str, ...] = ()
    decision: IntakeDecision | None = None
    # The resources the case was solved over (after canonicalization and skeptic adjustments).
    attributions: tuple[AttributedResource, ...] = ()


def build_context(mode: RunMode) -> Context:
    ledger = TokenLedger(paths.RUNS_DIR / "run_log.jsonl")
    llm = build_client(mode, DiskCache(paths.LLM_CACHE_DIR), ledger)
    report = IngestReport()
    templates = compile_rules(paths.DELETION_RULES)
    closure = IdentityClosure.from_csvs(paths.USER_IDENTITIES, paths.USER_AUDIT_LOG)
    lineage = LineageGraph.from_csv(paths.SYSTEM_ACTIVITY)
    activity_prefixes = frozenset(resource_prefix(c.resource_id) for c in lineage.claims)
    adapters = build_adapters(LLMPerceptionAgent(llm), mode, report, activity_prefixes)
    store = ingest_all(read_manifest(), adapters, report)
    deterministic = mode is RunMode.DETERMINISTIC
    # Built over every request in both files, so a case's answer never depends on --split.
    every_case = load_requests(paths.SAMPLE_REQUESTS) + load_requests(paths.DELETION_REQUESTS)
    llm_intake: LLMIntakeAgent | None = None
    if not deterministic:
        # Fixed batches over all 550 requests: one cache key set for every split.
        llm_intake = LLMIntakeAgent(llm)
        llm_intake.prepare([(c.case_id, c.request_text) for c in every_case])
    joint = tuple(
        JointActivityAttribution.build(every_case, closure, store, lineage, event_type=e, target_system=t)
        for e, t, _ in JOINT_PASSES
    )
    shared_picks = find_shared_picks(every_case, closure, store, lineage, joint)
    return Context(
        mode=mode,
        llm=llm,
        ledger=ledger,
        templates=templates,
        closure=closure,
        lineage=lineage,
        store=store,
        resolver=CategoryResolver(llm, {c: f"{t.condition} -> {t.action}" for c, t in templates.items()}),
        rule_intake=RuleIntakeAgent(),
        llm_intake=llm_intake,
        # The LLM skeptic is not implemented; every mode uses the advisory no-op.
        skeptic=NullSkepticAgent(),
        canonical=lineage.canonical_systems(),
        joint=joint,
        shared_picks=shared_picks,
        report=report,
    )


ACTIVITY_TIERS = (LinkTier.ACTIVITY_TEMPORAL, LinkTier.ACTIVITY_JOINT)


def find_shared_picks(
    cases: list[CaseRequest],
    closure: IdentityClosure,
    store: FactStore,
    lineage: LineageGraph,
    joint: tuple[JointActivityAttribution, ...],
) -> dict[str, tuple[str, ...]]:
    """Activity picks (temporal or joint) that would go to more than one subject, over every request.

    Built over both request files, so a case's answer never depends on --split.
    """
    holders: dict[str, dict[str, str]] = {}  # resource_id -> {subject: case_id}
    for case in cases:
        subject = closure.subject_of(normalize_email(case.email))
        if subject is None:
            continue
        picks = [a for a in ResourceAttribution.for_case(case, subject, closure, store, lineage) if a.tier in ACTIVITY_TIERS]
        picks += [j.assigned[case.case_id] for j in joint if case.case_id in j.assigned]
        for a in picks:
            holders.setdefault(a.resource_id, {}).setdefault(subject, case.case_id)
    return {rid: tuple(sorted(by.values())) for rid, by in sorted(holders.items()) if len(by) > 1}


def canonicalize(attributions: list[AttributedResource], ctx: Context) -> tuple[list[AttributedResource], list[str]]:
    """Every resource carries the system that owns its id prefix, never a replica or store name.

    Attribution can name one resource twice under different systems (a log
    names support_replica, activity_temporal names support_platform). After
    canonicalization they are one resource: the stronger tier is kept and the
    evidence of both is merged.
    """
    out: list[AttributedResource] = []
    flags: list[str] = []
    for a in attributions:
        vote = ctx.canonical.get(resource_prefix(a.resource_id))
        if vote is None:
            flags.append(f"system_unmapped:{a.resource_id}:{a.system_id}")
            out.append(a)
        elif vote.system_id != a.system_id:
            flags.append(f"system_canonical:{a.resource_id}:{a.system_id}->{vote.system_id}")
            out.append(replace(a, system_id=vote.system_id))
        else:
            out.append(a)
    merged: dict[str, AttributedResource] = {}
    for a in sorted(out, key=lambda a: (a.resource_id, a.tier)):
        kept = merged.get(a.resource_id)
        if kept is None:
            merged[a.resource_id] = a
            continue
        flags.append(f"attribution_merged:{a.resource_id}:{kept.tier.name}+{a.tier.name}")
        merged[a.resource_id] = replace(
            kept,
            reasons=tuple(sorted(set(kept.reasons) | set(a.reasons))),
            evidence_ids=tuple(sorted(set(kept.evidence_ids) | set(a.evidence_ids))),
            late_evidence_ids=tuple(sorted(set(kept.late_evidence_ids) | set(a.late_evidence_ids))),
            needs_skeptic=kept.needs_skeptic and a.needs_skeptic,
        )
    result = sorted(merged.values(), key=lambda a: (a.system_id, a.resource_id))
    return result, flags


def link_resources(
    case: CaseRequest, subject: str | None, ctx: Context, adjust: bool, flags: list[str]
) -> tuple[list[AttributedResource], list[AttributedResource]]:
    """(attributions, deferred fallback-only joint links) for a case, before canonicalization.

    Identifier attribution on the full store, then the joint activity passes,
    the shared-pick refusal and the skeptic's gate-accepted adjustments. Every
    refusal is appended to `flags`.
    """
    # Ownership uses the full store (time-invariant); state and citations use as_of(audit_at).
    # Attribution runs for every resolved subject so intake can be corroborated.
    attributions, rejected_links = (
        ResourceAttribution.for_case_detailed(case, subject, ctx.closure, ctx.store, ctx.lineage)
        if subject is not None
        else ([], [])
    )
    flags.extend(
        f"activity_pick_refused:{r.resource_id}:{r.conflict_fact}:{r.conflict_user}" for r in rejected_links if r.conflict_fact
    )
    deferred: list[AttributedResource] = []  # fallback_only joint links
    for (_, system, fallback_only), joint_pass in zip(JOINT_PASSES, ctx.joint, strict=True):
        if case.case_id in joint_pass.refused_holds:
            # The forced assignment found this activity_temporal pick contradicted by another request.
            held, why = joint_pass.refused_holds[case.case_id]
            flags.append(f"activity_pick_refused:{held}:forced_conflict:{why}")
            attributions = [a for a in attributions if not (a.tier in ACTIVITY_TIERS and a.resource_id == held)]
        joint = joint_pass.assigned.get(case.case_id)
        if joint is not None and not any(a.resource_id == joint.resource_id for a in attributions):
            (deferred if fallback_only else attributions).append(joint)
        elif case.case_id in joint_pass.rejected:
            flags.append(f"joint_none:{system}:{joint_pass.rejected[case.case_id]}")
    # A resource picked from activity rows for two different subjects belongs to neither pick.
    for a in [*attributions, *deferred]:
        sharers = ctx.shared_picks.get(a.resource_id)
        if a.tier in ACTIVITY_TIERS and sharers:
            flags.append(f"activity_pick_refused:{a.resource_id}:{a.evidence_ids[0] if a.evidence_ids else ''}:shared_by_{'+'.join(sharers)}")
    attributions = [a for a in attributions if not (a.tier in ACTIVITY_TIERS and a.resource_id in ctx.shared_picks)]
    deferred = [a for a in deferred if a.resource_id not in ctx.shared_picks]
    adj = ctx.skeptic_adjustments.get(case.case_id) if adjust else None
    if adj:
        # Accepted by the gate; the solver verifies the consequences.
        have = {a.resource_id for a in attributions}
        attributions = [a for a in attributions if a.resource_id not in adj.remove]
        attributions += [a for a in adj.add if a.resource_id not in have]
        deferred = [d for d in deferred if d.resource_id not in adj.remove]
        flags.extend(f"skeptic_accepted:REMOVE:{rid}" for rid in sorted(adj.remove))
        flags.extend(f"skeptic_accepted:ADD:{a.resource_id}" for a in adj.add)
    return attributions, deferred


def run_case(case: CaseRequest, ctx: Context, adjust: bool = True) -> CaseResult:
    rule_intake = ctx.rule_intake.classify(case.case_id, case.request_text)
    subject = ctx.closure.subject_of(normalize_email(case.email))
    flags: list[str] = []
    attributions, deferred = link_resources(case, subject, ctx, adjust, flags)
    base_attributions = attributions
    attributions, system_flags = canonicalize(attributions, ctx)
    flags.extend(system_flags)

    # Corroboration: CLASSIFY_REQUEST non-deletion rows on the subject's resources.
    owned = {a.resource_id for a in attributions}
    support = tuple(
        c.activity_id for c in ctx.lineage.non_deletion_classifications(case.audit_at) if c.resource_id in owned
    )
    llm_answer = ctx.llm_intake.answer(case.case_id) if ctx.llm_intake else None
    llm_rejected = ctx.llm_intake.rejected.get(case.case_id, "") if ctx.llm_intake else ""
    decision = decide_intake(rule_intake, llm_answer, support, llm_rejected)
    intake = decision.result
    flags.extend(decision.flags)
    if decision.source != "rules_only":
        flags.append(f"intake_source:{decision.source}")
    for activity_id in support:
        flags.append(
            f"intake_conflict:{activity_id}" if intake.authorizes_deletion else f"intake_support:{activity_id}"
        )

    store = ctx.store.as_of(case.audit_at)

    def solve_with(resources: list[AttributedResource]) -> Certificate:
        return solve(
            case,
            intake,
            subject,
            resources if intake.authorizes_deletion is not False else [],
            store,
            ctx.templates,
            ctx.lineage,
            full_store=ctx.store,
            resolver=ctx.resolver,
        )

    certificate = solve_with(attributions)
    if deferred and FALLBACK_FLAG in certificate.flags:
        attributions, system_flags = canonicalize(base_attributions + deferred, ctx)
        certificate = solve_with(attributions)
        flags.extend(f"joint_fallback:{a.system_id}:{a.resource_id}" for a in deferred)
    objections = ctx.skeptic.challenge(certificate, attributions)
    flags.extend(f"skeptic:{o.kind}:{o.target}" for o in objections)
    if certificate.verdict is not Verdict.NOT_APPLICABLE:
        skipped = case.case_id in ctx.skeptic_skipped
        for a in attributions:
            if a.needs_skeptic and not skipped:
                outcome = ctx.skeptic_outcomes.get(f"{case.case_id}:{a.resource_id}")
                flags.append(f"skeptic:{outcome}:{a.resource_id}" if outcome else f"skeptic_pending:{a.resource_id}")
        if skipped and any(a.needs_skeptic for a in attributions):
            flags.append("skeptic_skipped:budget")
    flags.extend(
        f"perception_skipped:{s.evidence_id}" for s in ctx.report.skipped if s.captured_at <= case.audit_at
    )
    steps = order_steps(project(certificate, store, case.audit_at))
    if certificate.verdict is Verdict.COMPLETE:
        emitted = {s.resource_id for s in steps}
        flags.extend(
            f"uncited:{o.resource_id}"
            for o in certificate.obligations
            if o.status is not ObligationStatus.DISCHARGED or o.resource_id not in emitted
        )
    return CaseResult(certificate.with_flags(tuple(flags)), steps, intake, support, decision, tuple(attributions))
