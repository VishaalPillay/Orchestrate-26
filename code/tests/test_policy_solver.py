"""compile_rules, the solver's state rule and the planner's citation rule, on synthetic facts.

Resource ids and evidence ids mirror sample cases (case_01/04 coverage citation,
case_32 tombstone, case_16 replica drift) but the facts are built here.
"""

from __future__ import annotations

from pcda import paths
from pcda.agents.intake import IntakeResult
from pcda.cases import CaseRequest
from pcda.category import CategoryResolver
from pcda.facts import Fact, FactKind, FactStore, parse_ts
from pcda.identity import AttributedResource, LinkTier
from pcda.lineage import LineageGraph
from pcda.planner import cite, order_steps, project
from pcda.policy import compile_rules
from pcda.solver import ABSENT, CLEARED, EXPOSED, PRESENT, ObligationStatus, Verdict, resource_state, solve

TEMPLATES = compile_rules(paths.DELETION_RULES)
AUDIT = parse_ts("2026-09-07T10:20:00Z")


def fact(
    fid: str,
    rid: str | None,
    kind: FactKind,
    at: str,
    *,
    system: str | None = "account_service",
    evidence: str | None = None,
    **attrs: str,
) -> Fact:
    t = parse_ts(at)
    return Fact(fid, evidence or fid.split(":")[0], t, t, system, rid, None, kind, frozenset(), attrs)


def res(rid: str, system: str = "account_service", late: tuple[str, ...] = ()) -> AttributedResource:
    return AttributedResource(rid, system, LinkTier.DB_ROW, ("test",), (), late_evidence_ids=late)


def case(audit: str = "2026-09-07T10:20:00Z") -> CaseRequest:
    t = parse_ts(audit)
    return CaseRequest("case_x", "a@b.co", parse_ts("2026-09-01T00:00:00Z"), "COMPLETE", t, t, "delete")


DELETION = IntakeResult("case_x", True, "en", "DELETION")


def test_compile_rules_shape() -> None:
    assert TEMPLATES["BACKUP_ARCHIVE"].action == "RETAIN"
    assert TEMPLATES["BACKUP_ARCHIVE"].required_state == "BLOCKED"
    assert TEMPLATES["SECURITY_AUDIT_LOG"].required_state == "PRESENT"
    assert TEMPLATES["LEGAL_HOLD_RECORD"].required_state == "PRESENT"
    assert TEMPLATES["ACCOUNT_TOMBSTONE"].action == "REDACT"
    assert TEMPLATES["DATA_FLOW"].required_state == "DISABLED"
    assert len(TEMPLATES) == 16


def test_present_in_any_replica_wins_a_tie() -> None:
    store = FactStore(
        [
            fact("ev_a:1", "account_994", FactKind.ABSENCE, "2026-09-05T10:00:00Z", table="customer_data_primary"),
            fact("ev_a:2", "account_994", FactKind.PRESENCE, "2026-09-05T10:00:00Z", table="customer_data_read_replica"),
        ]
    )
    reading, _, _ = resource_state(res("account_994"), TEMPLATES["USER_PROFILE"], store)
    assert reading == PRESENT


def test_latest_observation_wins() -> None:
    store = FactStore(
        [
            fact("ev_a:1", "account_1", FactKind.PRESENCE, "2026-09-01T10:00:00Z"),
            fact("ev_b:1", "account_1", FactKind.ABSENCE, "2026-09-03T10:00:00Z"),
        ]
    )
    reading, f, _ = resource_state(res("account_1"), TEMPLATES["USER_PROFILE"], store)
    assert (reading, f.evidence_id) == (ABSENT, "ev_b")


def test_tombstone_redact_by_pii_fields() -> None:
    exposed = fact("ev:1", "tombstone_9178", FactKind.PRESENCE, "2026-09-05T12:00:00Z",
                   system="identity_service", pii_fields="name,email,phone,email_hash")
    cleared = fact("ev:2", "tombstone_440", FactKind.PRESENCE, "2026-09-05T12:00:00Z", system="identity_service")
    store = FactStore([exposed, cleared])
    t = TEMPLATES["ACCOUNT_TOMBSTONE"]
    assert resource_state(res("tombstone_9178", "identity_service"), t, store)[0] == EXPOSED
    assert resource_state(res("tombstone_440", "identity_service"), t, store)[0] == CLEARED


def test_absence_cites_latest_non_vacuous_covering_snapshot() -> None:
    store = FactStore(
        [
            fact("evidence_54:c", None, FactKind.ABSENCE, "2026-09-02T23:00:00Z", coverage="true"),
            fact("evidence_90:c", None, FactKind.ABSENCE, "2026-09-05T07:18:00Z", coverage="true", is_vacuous="true"),
            fact("evidence_99:c", None, FactKind.ABSENCE, "2026-09-08T00:00:00Z", coverage="true"),  # after audit
        ]
    ).as_of(AUDIT)
    cert = solve(case(), DELETION, "user_04", [res("account_004")], store, TEMPLATES, LineageGraph([]),
                 full_store=store, resolver=CategoryResolver(None))
    # No fact names account_004, so its category comes from nothing: unknown, flagged.
    assert cert.verdict is Verdict.INCOMPLETE  # deletion was requested; completion cannot be proven
    assert "category_unknown:account_004" in cert.flags
    assert "fallback_no_provable_obligation" in cert.flags  # nothing provable: the INCOMPLETE fallback

    full = FactStore(list(store) + [fact("ev_old:1", "account_004", FactKind.LINK, "2026-01-01T00:00:00Z",
                                         table="customer_profiles")])
    cert = solve(case(), DELETION, "user_04", [res("account_004")], store, TEMPLATES, LineageGraph([]),
                 full_store=full, resolver=CategoryResolver(None))
    (ob,) = cert.obligations
    assert ob.status is ObligationStatus.DISCHARGED and ob.via_coverage
    assert cite(ob, store, AUDIT) == "evidence_54"
    steps = order_steps(project(cert, store, AUDIT))
    assert [(s.operation, s.evidence_id) for s in steps] == [("VERIFY_RECORD_ABSENT", "evidence_54")]


def test_incomplete_projection_and_late_evidence_never_cited() -> None:
    store = FactStore(
        [
            fact("ev_a:1", "search_001", FactKind.PRESENCE, "2026-09-05T10:00:00Z", system="search_service",
                 table="search_documents_primary"),
            fact("ev_a:2", "account_006", FactKind.ABSENCE, "2026-09-05T10:00:00Z", table="profile_lookups"),
            fact("ev_a:3", "audit_record_1", FactKind.PRESENCE, "2026-09-05T10:00:00Z", system="security_service",
                 table="audit_records"),
        ]
    )
    attributed = [res("account_006"), res("audit_record_1", "security_service"), res("search_001", "search_service")]
    cert = solve(case(), DELETION, "user_06", attributed, store, TEMPLATES, LineageGraph([]),
                 full_store=store, resolver=CategoryResolver(None))
    assert cert.verdict is Verdict.INCOMPLETE
    steps = order_steps(project(cert, store, AUDIT))
    assert [(s.operation, s.resource_id, s.evidence_id) for s in steps] == [
        ("REPORT_REMAINING_RECORD", "search_001", "ev_a"),
        ("DELETE_RECORD", "search_001", ""),
        ("VERIFY_RECORD_ABSENT", "account_006", ""),
        ("VERIFY_RECORD_ABSENT", "search_001", ""),
        ("VERIFY_RECORD_PRESENT", "audit_record_1", ""),
    ]
    late = res("search_001", "search_service", late=("ev_a",))
    ob = next(o for o in cert.obligations if o.resource_id == "search_001")
    assert cite(type(ob)(late, ob.category, ob.template, ob.status), store, AUDIT) == ""


def test_guard_with_existence_but_no_state_is_open_risk() -> None:
    """Burden of proof: a backup seen at audit_at with no restore setting is a restore risk."""
    seen = fact("evidence_32:1", "backup_9287", FactKind.PRESENCE, "2026-09-05T10:00:00Z",
                system="backup_service", table="backup_catalog")
    store = FactStore([seen])
    cert = solve(case(), DELETION, "user_x", [res("backup_9287", "backup_service")], store, TEMPLATES,
                 LineageGraph([]), full_store=store, resolver=CategoryResolver(None))
    (ob,) = cert.obligations
    assert ob.status is ObligationStatus.OPEN and ob.violated_by is seen
    assert cert.verdict is Verdict.INCOMPLETE and "unproven_guard:backup_9287" in cert.flags
    steps = order_steps(project(cert, store, AUDIT))
    assert [(s.operation, s.evidence_id) for s in steps] == [
        ("REPORT_RESTORE_RISK", "evidence_32"), ("BLOCK_RESTORE", ""), ("VERIFY_RESTORE_BLOCKED", "")
    ]
    # Nothing evidences existence: still UNKNOWN, never an open risk.
    empty = FactStore([])
    cert = solve(case(), DELETION, "user_x", [res("backup_9287", "backup_service")], empty, TEMPLATES,
                 LineageGraph([]), full_store=store, resolver=CategoryResolver(None))
    assert cert.obligations[0].status is ObligationStatus.UNKNOWN


def test_fallback_is_incomplete_with_no_steps_and_not_applicable_stays_an_intake_outcome() -> None:
    """Deletion requested, nothing provable: INCOMPLETE, flagged, no steps. Only intake yields NOT_APPLICABLE."""
    store = FactStore([]).as_of(AUDIT)
    resolver = CategoryResolver(None)
    cert = solve(case(), DELETION, "user_07", [res("object_19", "object_storage")], store, TEMPLATES, LineageGraph([]),
                 full_store=store, resolver=resolver)
    assert cert.verdict is Verdict.INCOMPLETE
    assert "fallback_no_provable_obligation" in cert.flags
    assert not any(o.status is ObligationStatus.OPEN for o in cert.obligations)
    assert project(cert, store, AUDIT) == []

    refusal = IntakeResult("case_x", False, "en", "ACCESS")
    cert = solve(case(), refusal, "user_07", [res("object_19", "object_storage")], store, TEMPLATES, LineageGraph([]),
                 full_store=store, resolver=resolver)
    assert cert.verdict is Verdict.NOT_APPLICABLE
    assert "fallback_no_provable_obligation" not in cert.flags
