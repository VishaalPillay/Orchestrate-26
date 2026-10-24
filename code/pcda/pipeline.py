"""The control flow, in plain code. Global context is built once; each case is
a fixed sequence of stage calls with no framework in between:

  intake -> subject -> as_of(audit_at) -> attribution -> solve -> skeptic -> project
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pcda import paths
from pcda.agents.intake import IntakeAgent, LLMIntakeAgent
from pcda.agents.perception import LLMPerceptionAgent
from pcda.agents.skeptic import LLMSkepticAgent, SkepticAgent
from pcda.cases import CaseRequest
from pcda.category import CategoryResolver
from pcda.facts import FactStore
from pcda.identity import IdentityClosure, ResourceAttribution, normalize_email
from pcda.ingest.base import IngestReport, read_manifest
from pcda.ingest.registry import build_adapters, ingest_all
from pcda.lineage import LineageGraph
from pcda.llm.cache import DiskCache
from pcda.llm.client import LLMClient, RunMode, build_client
from pcda.llm.ledger import TokenLedger
from pcda.planner import Step, order_steps, project
from pcda.policy import ObligationTemplate, compile_rules
from pcda.solver import Certificate, solve


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
    report: IngestReport = field(default_factory=IngestReport)


@dataclass(frozen=True)
class CaseResult:
    certificate: Certificate
    steps: list[Step]


def build_context(mode: RunMode) -> Context:
    ledger = TokenLedger()
    llm = build_client(mode, DiskCache(paths.LLM_CACHE_DIR), ledger)
    report = IngestReport()
    templates = compile_rules(paths.DELETION_RULES)
    closure = IdentityClosure.from_csvs(paths.USER_IDENTITIES, paths.USER_AUDIT_LOG)
    lineage = LineageGraph.from_csv(paths.SYSTEM_ACTIVITY)
    adapters = build_adapters(LLMPerceptionAgent(llm), mode, report)
    store = ingest_all(read_manifest(), adapters, report)
    return Context(
        mode=mode,
        llm=llm,
        ledger=ledger,
        templates=templates,
        closure=closure,
        lineage=lineage,
        store=store,
        resolver=CategoryResolver(llm),
        intake=LLMIntakeAgent(llm),
        skeptic=LLMSkepticAgent(llm),
        report=report,
    )


def run_case(case: CaseRequest, ctx: Context) -> CaseResult:
    intake = ctx.intake.classify(case.case_id, case.request_text)
    subject = ctx.closure.subject_of(normalize_email(case.email))
    # Ownership uses the full store (time-invariant); state and citations use `store`.
    store = ctx.store.as_of(case.audit_at)
    attributions = (
        ResourceAttribution.for_case(case, subject, ctx.closure, ctx.store, ctx.lineage)
        if subject is not None and intake.authorizes_deletion
        else []
    )
    certificate = solve(case, intake, subject, attributions, store, ctx.templates, ctx.lineage)
    objections = ctx.skeptic.challenge(certificate, attributions)
    if objections:
        certificate = Certificate(
            certificate.case_id,
            certificate.subject,
            certificate.obligations,
            certificate.verdict,
            certificate.flags + tuple(f"skeptic:{o.kind}:{o.target}" for o in objections),
        )
    skipped = tuple(
        f"perception_skipped:{s.evidence_id}"
        for s in ctx.report.skipped
        if s.captured_at <= case.audit_at
    )
    if skipped:
        certificate = Certificate(
            certificate.case_id,
            certificate.subject,
            certificate.obligations,
            certificate.verdict,
            certificate.flags + skipped,
        )
    steps = order_steps(project(certificate, store, case.audit_at))
    return CaseResult(certificate, steps)
