"""The control flow, in plain code. Global context is built once; each case is
a fixed sequence of stage calls with no framework in between:

  intake -> subject -> attribution (full store) -> canonical system ->
  intake corroboration -> as_of(audit_at) -> solve -> skeptic -> project
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from pcda import paths
from pcda.agents.intake import IntakeAgent, IntakeResult, LLMIntakeAgent, RuleIntakeAgent
from pcda.agents.perception import LLMPerceptionAgent
from pcda.agents.skeptic import LLMSkepticAgent, NullSkepticAgent, SkepticAgent
from pcda.cases import CaseRequest, load_requests
from pcda.category import CategoryResolver
from pcda.facts import FactStore
from pcda.identity import (
    AttributedResource,
    IdentityClosure,
    JointActivityAttribution,
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
JOINT_PASSES: tuple[tuple[str, str], ...] = (
    ("DELETE_RECORD", "account_service"),
    ("ANONYMIZE_RECORD", "analytics_platform"),
)


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
    intake: IntakeAgent
    skeptic: SkepticAgent
    canonical: dict[str, SystemVote] = field(default_factory=dict)
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


def build_context(mode: RunMode) -> Context:
    ledger = TokenLedger()
    llm = build_client(mode, DiskCache(paths.LLM_CACHE_DIR), ledger)
    report = IngestReport()
    templates = compile_rules(paths.DELETION_RULES)
    closure = IdentityClosure.from_csvs(paths.USER_IDENTITIES, paths.USER_AUDIT_LOG)
    lineage = LineageGraph.from_csv(paths.SYSTEM_ACTIVITY)
    adapters = build_adapters(LLMPerceptionAgent(llm), mode, report)
    store = ingest_all(read_manifest(), adapters, report)
    deterministic = mode is RunMode.DETERMINISTIC
    # Built over every request in both files, so a case's answer never depends on --split.
    every_case = load_requests(paths.SAMPLE_REQUESTS) + load_requests(paths.DELETION_REQUESTS)
    joint = tuple(
        JointActivityAttribution.build(every_case, closure, store, lineage, event_type=e, target_system=t)
        for e, t in JOINT_PASSES
    )
    return Context(
        mode=mode,
        llm=llm,
        ledger=ledger,
        templates=templates,
        closure=closure,
        lineage=lineage,
        store=store,
        resolver=CategoryResolver(llm),
        intake=RuleIntakeAgent() if deterministic else LLMIntakeAgent(llm),
        skeptic=NullSkepticAgent() if deterministic else LLMSkepticAgent(llm),
        canonical=lineage.canonical_systems(),
        joint=joint,
        report=report,
    )


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


def run_case(case: CaseRequest, ctx: Context) -> CaseResult:
    intake = ctx.intake.classify(case.case_id, case.request_text)
    subject = ctx.closure.subject_of(normalize_email(case.email))
    flags: list[str] = []
    # Ownership uses the full store (time-invariant); state and citations use `store`.
    # Attribution runs for every resolved subject so intake can be corroborated.
    attributions = (
        ResourceAttribution.for_case(case, subject, ctx.closure, ctx.store, ctx.lineage)
        if subject is not None
        else []
    )
    for (_, system), joint_pass in zip(JOINT_PASSES, ctx.joint):
        joint = joint_pass.assigned.get(case.case_id)
        if joint is not None and not any(a.resource_id == joint.resource_id for a in attributions):
            attributions.append(joint)
        elif case.case_id in joint_pass.rejected:
            flags.append(f"joint_none:{system}:{joint_pass.rejected[case.case_id]}")
    attributions, system_flags = canonicalize(attributions, ctx)
    flags.extend(system_flags)

    # Corroboration only: text stays the decision source in deterministic mode.
    owned = {a.resource_id for a in attributions}
    support = tuple(
        c.activity_id for c in ctx.lineage.non_deletion_classifications(case.audit_at) if c.resource_id in owned
    )
    for activity_id in support:
        flags.append(
            f"intake_conflict:{activity_id}" if intake.authorizes_deletion else f"intake_support:{activity_id}"
        )

    store = ctx.store.as_of(case.audit_at)
    certificate = solve(
        case,
        intake,
        subject,
        attributions if intake.authorizes_deletion is not False else [],
        store,
        ctx.templates,
        ctx.lineage,
        full_store=ctx.store,
        resolver=ctx.resolver,
    )
    objections = ctx.skeptic.challenge(certificate, attributions)
    flags.extend(f"skeptic:{o.kind}:{o.target}" for o in objections)
    if certificate.verdict is not Verdict.NOT_APPLICABLE:
        flags.extend(f"skeptic_pending:{a.resource_id}" for a in attributions if a.needs_skeptic)
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
    return CaseResult(certificate.with_flags(tuple(flags)), steps, intake, support)
