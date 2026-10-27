"""IdentityClosure and ResourceAttribution: rows from the dataset, plus synthetic claims for the guardrails."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from pcda import paths
from pcda.cases import CaseRequest
from pcda.facts import parse_ts
from pcda.identity import (
    ACTIVITY_WINDOW,
    AttributedResource,
    IdentityClosure,
    LinkTier,
    ResourceAttribution,
    email_hash,
)
from pcda.lineage import Claim, LineageGraph

# user_523 from user_identities.csv / user_audit_log.csv (audit_59, audit_60), verbatim.
IDENTITIES = """user_id,name,email,phone,email_hash,updated_at
user_523,Oliver Walker,o.walker66@yahoo.com,"+1 (646) 555-0123",9c40bdbab4944a7f0e06bb210e8990ff340ee998813b17e432000a70f5dfe554,2025-09-30T21:50:27Z
user_524,Other Person,other@example.com,+44 7700 900111,,2025-09-30T21:50:27Z
"""
AUDIT = """audit_id,user_id,event_time,field_name,old_value,new_value
audit_59,user_523,2025-09-30T21:50:27Z,email,o.walker6695@yahoo.com,o.walker66@yahoo.com
audit_60,user_523,2025-09-30T21:50:27Z,email_hash,6570536e0f14e7262d2b39a7d7968f30a4f7ee35f260591a95a3a54c313363a8,9c40bdbab4944a7f0e06bb210e8990ff340ee998813b17e432000a70f5dfe554
"""


@pytest.fixture
def closure(tmp_path: Path) -> IdentityClosure:
    (tmp_path / "ids.csv").write_text(IDENTITIES, encoding="utf-8")
    (tmp_path / "audit.csv").write_text(AUDIT, encoding="utf-8")
    return IdentityClosure.from_csvs(tmp_path / "ids.csv", tmp_path / "audit.csv")


def test_closure_resolves_current_and_historical_identifiers(closure):
    old_hash = "6570536e0f14e7262d2b39a7d7968f30a4f7ee35f260591a95a3a54c313363a8"
    for ident in (
        "O.Walker66@Yahoo.com",  # current email, any case
        "o.walker6695@yahoo.com",  # historical email from the audit log
        "+1 (646) 555-0123",  # phone, formatting stripped
        "16465550123",
        old_hash,  # historical hash
        email_hash("o.walker6695@yahoo.com"),  # computed from the historical email
        "user_523",
    ):
        assert closure.subject_of(ident) == "user_523", ident
    assert old_hash == email_hash("o.walker6695@yahoo.com")
    assert closure.subject_of("other@example.com") == "user_524"
    assert closure.subject_of("o.walker@yahoo.com") is None  # exact matches only
    assert closure.subject_of("not an identifier") is None
    assert {"user_523", "o.walker6695@yahoo.com", "16465550123", old_hash} <= closure.identifiers_of("user_523")
    assert closure.identifiers_of("user_523").isdisjoint(closure.identifiers_of("user_524"))
    assert closure.conflicts == ()


def test_identifier_claimed_by_two_users_is_never_unioned(tmp_path):
    (tmp_path / "ids.csv").write_text(
        "user_id,name,email,phone,email_hash,updated_at\n"
        "user_1,A,shared@example.com,+1 555 000 1111,,2025-01-01T00:00:00Z\n"
        "user_2,B,shared@example.com,+1 555 000 2222,,2025-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    (tmp_path / "audit.csv").write_text("audit_id,user_id,event_time,field_name,old_value,new_value\n", encoding="utf-8")
    c = IdentityClosure.from_csvs(tmp_path / "ids.csv", tmp_path / "audit.csv")
    assert c.subject_of("shared@example.com") == "user_1"
    # The email and its derived sha256 both collide; neither is unioned for user_2.
    assert {(i, kept, rejected) for i, kept, rejected in c.conflicts} == {
        ("shared@example.com", "user_1", "user_2"),
        (email_hash("shared@example.com"), "user_1", "user_2"),
    }
    assert "user_1" not in c.identifiers_of("user_2")


# --- activity_temporal guardrails (synthetic claims, real row shape) --------------------------------

REPORTED = parse_ts("2026-09-05T12:00:00Z")
CASE = CaseRequest(
    "case_x", "a@example.com", parse_ts("2026-09-04T12:00:00Z"), "COMPLETE", REPORTED,
    parse_ts("2026-09-15T12:00:00Z"), "delete me",
)


def claim(n: int, minutes: float, *, source="deletion_worker", target="account_service",
          event="DELETE_RECORD", rid="account_1") -> Claim:
    return Claim(f"activity_{n}", REPORTED + timedelta(minutes=minutes), event, source, target, rid, "SUCCESS", "")


def temporal(claims: list[Claim], stronger: list[AttributedResource] = ()):
    return ResourceAttribution.activity_temporal(CASE, LineageGraph(claims), list(stronger))


def test_activity_temporal_accepts_the_sole_in_window_delete_and_flags_it():
    (a,) = temporal([claim(1, 3.0), claim(2, 400.0, rid="account_9")])
    assert (a.resource_id, a.system_id, a.tier) == ("account_1", "account_service", LinkTier.ACTIVITY_TEMPORAL)
    assert a.needs_skeptic and a.evidence_ids == ("activity_1",)


@pytest.mark.parametrize(
    "claims",
    [
        [claim(1, 3.0, source="profile_service")],  # (a) wrong actor
        [claim(1, 3.0, event="ANONYMIZE_RECORD")],  # (a) wrong operation
        [claim(1, ACTIVITY_WINDOW.total_seconds() / 60 + 1)],  # (b) outside the window
        [claim(1, 3.0), claim(2, -4.0, rid="account_2")],  # (c) two candidates for the system
    ],
    ids=["actor", "operation", "window", "two-candidates"],
)
def test_activity_temporal_guardrails_reject(claims):
    assert temporal(claims) == []


def test_activity_temporal_yields_to_a_stronger_link_on_the_same_system():
    stronger = AttributedResource("account_77", "account_service", LinkTier.DB_ROW, (), ("evidence_1",))
    assert temporal([claim(1, 3.0)], [stronger]) == []
    other = AttributedResource("search_1", "search_service", LinkTier.DB_ROW, (), ("evidence_1",))
    assert len(temporal([claim(1, 3.0)], [other])) == 1  # a link on another system does not block it


def test_activity_after_audit_is_not_knowable():
    late = claim(1, 0.0)
    object.__setattr__(late, "event_time", CASE.audit_at + timedelta(minutes=1))
    case = CaseRequest("case_y", "a@example.com", CASE.requested_at, "COMPLETE", CASE.audit_at, CASE.audit_at, "x")
    assert ResourceAttribution.activity_temporal(case, LineageGraph([late]), []) == []


# --- against the dataset: the case_17 decoy ---------------------------------------------------------

needs_dataset = pytest.mark.skipif(not paths.EVIDENCE_MANIFEST.exists(), reason="dataset/ not present")


@needs_dataset
def test_case17_decoy_is_stale_and_real_account_is_attributed():
    from pcda.agents.perception import LLMPerceptionAgent
    from pcda.cases import load_requests
    from pcda.ingest.base import IngestReport, read_manifest
    from pcda.ingest.registry import build_adapters, ingest_all
    from pcda.llm.client import RunMode

    report = IngestReport()
    full = ingest_all(read_manifest(), build_adapters(LLMPerceptionAgent(None), RunMode.DETERMINISTIC, report), report)  # type: ignore[arg-type]
    closure = IdentityClosure.from_csvs(paths.USER_IDENTITIES, paths.USER_AUDIT_LOG)
    lineage = LineageGraph.from_csv(paths.SYSTEM_ACTIVITY)
    case = next(c for c in load_requests(paths.SAMPLE_REQUESTS) if c.case_id == "case_17")
    user = closure.subject_of(case.email)
    accepted, stale = ResourceAttribution.for_case_detailed(case, user, closure, full.as_of(case.audit_at), lineage)
    ids = {a.resource_id: a for a in accepted}
    assert {"account_4092", "backup_3245", "audit_record_1797"} <= set(ids)
    assert "account_6523" not in ids
    assert "account_6523" in {a.resource_id for a in stale}
    assert "stale" in [a for a in stale if a.resource_id == "account_6523"][0].reasons[-1]
    assert ids["account_4092"].tier is LinkTier.LOG_TRACE


# --- ownership/state split ---------------------------------------------------------------------------


def _fact(fact_id: str, rid: str, observed: str, captured: str, ident: str = "a@example.com"):
    from pcda.facts import Fact, FactKind

    return Fact(
        fact_id=fact_id, evidence_id="evidence_x", captured_at=parse_ts(captured),
        observed_at=parse_ts(observed), system_id="account_service", resource_id=rid,
        category=None, kind=FactKind.PRESENCE, identifiers=frozenset({ident}),
        attrs={"src": "db_row", "table": "customer_profiles"},
    )


def _closure_for(tmp_path: Path) -> IdentityClosure:
    (tmp_path / "i.csv").write_text(
        "user_id,name,email,phone,email_hash,updated_at\nuser_1,A,a@example.com,+1 555 000 1111,,2025-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    (tmp_path / "a.csv").write_text("audit_id,user_id,event_time,field_name,old_value,new_value\n", encoding="utf-8")
    return IdentityClosure.from_csvs(tmp_path / "i.csv", tmp_path / "a.csv")


def test_ownership_may_come_from_a_file_captured_after_audit_and_is_not_citable(tmp_path):
    from pcda.facts import FactStore

    # Observed before the audit (09-15 12:00 == audit_at) but delivered in a file captured 09-17.
    store = FactStore([_fact("f1", "audit_record_1", "2026-09-15T12:00:00Z", "2026-09-17T12:00:00Z")])
    accepted, rejected = ResourceAttribution.for_case_detailed(
        CASE, "user_1", _closure_for(tmp_path), store, LineageGraph([])
    )
    (a,) = accepted
    assert a.resource_id == "audit_record_1" and rejected == []
    assert a.evidence_ids == () and a.late_evidence_ids == ("evidence_x",)  # never citable


def test_resource_first_seen_after_audit_is_not_pulled_in(tmp_path):
    from pcda.facts import FactStore

    store = FactStore([_fact("f1", "account_new", "2026-09-16T00:00:00Z", "2026-09-17T12:00:00Z")])
    accepted, rejected = ResourceAttribution.for_case_detailed(
        CASE, "user_1", _closure_for(tmp_path), store, LineageGraph([])
    )
    assert accepted == []
    assert [r.resource_id for r in rejected] == ["account_new"] and "post-audit" in rejected[0].reasons[-1]
    # An activity row at or before the audit anchors it.
    row = claim(1, -60.0, rid="account_new")
    accepted, _ = ResourceAttribution.for_case_detailed(
        CASE, "user_1", _closure_for(tmp_path), store, LineageGraph([row])
    )
    assert [a.resource_id for a in accepted] == ["account_new"]


# --- activity_joint: accounts linked only by a deletion_worker row -----------------------------------


def _joint_setup(tmp_path: Path, extra_case: bool):
    from pcda.facts import FactStore
    from pcda.identity import JointActivityAttribution

    users = ["a", "b", "c", "d"]
    (tmp_path / "i.csv").write_text(
        "user_id,name,email,phone,email_hash,updated_at\n"
        + "".join(f"user_{u},{u},{u}@example.com,,,2025-01-01T00:00:00Z\n" for u in users),
        encoding="utf-8",
    )
    (tmp_path / "a.csv").write_text("audit_id,user_id,event_time,field_name,old_value,new_value\n", encoding="utf-8")
    closure = IdentityClosure.from_csvs(tmp_path / "i.csv", tmp_path / "a.csv")

    def case(cid: str, user: str, status: str) -> CaseRequest:
        return CaseRequest(cid, f"{user}@example.com", parse_ts("2026-09-04T12:00:00Z"), status, REPORTED,
                           parse_ts("2026-09-15T12:00:00Z"), "delete me")

    def row(n: int, minutes: float, rid: str, result: str) -> Claim:
        return Claim(f"activity_{n}", REPORTED + timedelta(minutes=minutes), "DELETE_RECORD", "deletion_worker",
                     "account_service", rid, result, "")

    claims = [row(1, -50, "account_1", "SUCCESS"), row(2, -60, "account_2", "FAILED"),
              row(3, -40, "account_3", "SUCCESS")]
    # account_3 belongs to user_c by identifier evidence; nobody else may take it.
    store = FactStore([_fact("evidence_x:1", "account_3", "2026-09-05T10:00:00Z", "2026-09-05T10:00:00Z",
                             ident="c@example.com")])
    cases = [case("case_a", "a", "COMPLETE"), case("case_b", "b", "NOT_STARTED"), case("case_c", "c", "COMPLETE")]
    if extra_case:
        cases.append(case("case_d", "d", "COMPLETE"))
    return JointActivityAttribution.build(cases, closure, store, LineageGraph(claims))


def test_activity_joint_assigns_by_status_result_and_elimination(tmp_path):
    joint = _joint_setup(tmp_path, extra_case=False)
    assert {cid: a.resource_id for cid, a in joint.assigned.items()} == {"case_a": "account_1", "case_b": "account_2"}
    assert all(a.tier == LinkTier.ACTIVITY_JOINT and a.needs_skeptic for a in joint.assigned.values())
    assert joint.assigned["case_a"].evidence_ids == ("activity_1",)


def test_activity_joint_refuses_an_account_two_cases_would_claim(tmp_path):
    joint = _joint_setup(tmp_path, extra_case=True)
    assert "case_a" not in joint.assigned and "case_d" not in joint.assigned
    assert "also the only candidate" in joint.rejected["case_a"]
    assert joint.assigned["case_b"].resource_id == "account_2"


# --- propagate: forced assignment ---------------------------------------------------------------


def test_propagate_is_order_independent_and_refuses_contradictions():
    from pcda.identity import propagate

    resource_of = {"r1": "acc_1", "r2": "acc_2", "r3": "acc_3"}
    subject_of = {"a": "user_a", "b": "user_b", "c": "user_c", "d": "user_d"}
    cands = {"a": frozenset({"r1"}), "b": frozenset({"r1", "r2"}), "c": frozenset({"r3"}), "d": frozenset({"r3"})}
    results = set()
    for order in (["a", "b", "c", "d"], ["d", "c", "b", "a"], ["b", "d", "a", "c"]):
        out = propagate({k: cands[k] for k in order}, resource_of, subject_of, eligible=frozenset(cands), two_sided=False)
        results.add((tuple(out.assigned.items()), tuple(out.refused.items())))
    assert len(results) == 1
    out = propagate(cands, resource_of, subject_of, eligible=frozenset(cands), two_sided=False)
    # a is forced to acc_1, which leaves b with acc_2; c and d contradict each other on acc_3.
    assert out.assigned == {"a": "acc_1", "b": "acc_2"}
    assert set(out.refused) == {"c", "d"} and "also the only candidate" in out.refused["c"]


def test_propagate_two_sided_needs_the_row_forced_too():
    from pcda.identity import propagate

    resource_of = {"r1": "an_1", "r2": "an_2"}
    subject_of = {"a": "user_a", "b": "user_b", "x": "user_x"}
    # b wants an_1 and an_2, so an_1 is not forced to a; x competes but is not eligible.
    cands = {"a": frozenset({"r1"}), "b": frozenset({"r1", "r2"}), "x": frozenset({"r2"})}
    out = propagate(cands, resource_of, subject_of, eligible=frozenset({"a", "b"}), two_sided=True)
    assert out.assigned == {}
    out = propagate({"a": frozenset({"r1"}), "x": frozenset({"r2"})}, resource_of, subject_of,
                    eligible=frozenset({"a"}), two_sided=True)
    assert out.assigned == {"a": "an_1"}


def test_staleness_gate_spares_a_match_on_the_stable_user_id(tmp_path):
    from pcda.facts import FactStore

    # Both observed long before requested_at: an email can be recycled, a user_id cannot.
    store = FactStore([
        _fact("f1", "audit_record_old", "2026-01-10T00:00:00Z", "2026-01-10T00:00:00Z", ident="user_1"),
        _fact("f2", "account_old", "2026-01-10T00:00:00Z", "2026-01-10T00:00:00Z", ident="a@example.com"),
    ])
    accepted, rejected = ResourceAttribution.for_case_detailed(
        CASE, "user_1", _closure_for(tmp_path), store, LineageGraph([])
    )
    assert [a.resource_id for a in accepted] == ["audit_record_old"]
    assert [r.resource_id for r in rejected] == ["account_old"] and "stale" in rejected[0].reasons[-1]
