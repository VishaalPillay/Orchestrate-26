"""Ownership exclusivity: an activity-derived pick is refused when an ownership-eligible fact ties the
resource to another user, or when two different subjects would receive it. Identifier links are untouched."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from pcda.cases import CaseRequest
from pcda.facts import Fact, FactKind, FactStore, parse_ts
from pcda.identity import (
    IdentityClosure,
    LinkTier,
    ResourceAttribution,
    UnionFind,
    conflicting_owner,
)
from pcda.lineage import Claim, LineageGraph
from pcda.pipeline import find_shared_picks

T0 = parse_ts("2026-03-10T12:00:00Z")


def closure() -> IdentityClosure:
    uf = UnionFind()
    users = {"a@x.com": "user_1", "b@x.com": "user_2", "c@x.com": "user_3"}
    for ident, user in users.items():
        uf.union(user, ident)
    return IdentityClosure(uf, dict(users, user_1="user_1", user_2="user_2", user_3="user_3"))


def fact(fid: str, rid: str, idents: set[str], kind: FactKind = FactKind.PRESENCE, **attrs: str) -> Fact:
    system = "export_service" if rid.startswith("export") else "search_service"
    return Fact(
        fid, fid.split(":")[0], T0 + timedelta(days=1), T0 + timedelta(days=1), system, rid, None, kind,
        frozenset(idents), attrs,
    )


def delete_row(aid: str, rid: str, minutes: float, system: str = "search_service") -> Claim:
    return Claim(aid, T0 + timedelta(minutes=minutes), "DELETE_RECORD", "deletion_worker", system, rid, "SUCCESS", "")


def case(cid: str, email: str) -> CaseRequest:
    return CaseRequest(cid, email, T0 - timedelta(days=1), "COMPLETE", T0, T0 + timedelta(days=3), "delete me")


def test_conflicting_owner_needs_an_eligible_fact_that_resolves_only_to_others() -> None:
    c = closure()
    store = FactStore(
        [
            fact("e1:1", "search_1", {"b@x.com"}),
            fact("e2:1", "search_2", {"a@x.com", "b@x.com"}),  # ambiguous: also the subject's
            fact("e3:1", "search_3", {"b@x.com"}, FactKind.ABSENCE),  # a miss says nothing about ownership
            fact("e4:1", "search_9", {"c@x.com"}, via="source_resource_id", linked_resource="search_4"),
            fact("e5:1", "search_5", {"nobody@x.com"}),  # resolves to no user
        ]
    )
    other, f = conflicting_owner("search_1", "user_1", c, store) or ("", None)
    assert other == "user_2" and f is not None and f.fact_id == "e1:1"
    assert conflicting_owner("search_1", "user_2", c, store) is None  # the owner's own pick is fine
    assert conflicting_owner("search_2", "user_1", c, store) is None
    assert conflicting_owner("search_3", "user_1", c, store) is None
    linked = conflicting_owner("search_4", "user_1", c, store)
    assert linked is not None and linked[0] == "user_3"  # the record derived from search_4 is user_3's
    assert conflicting_owner("search_5", "user_1", c, store) is None


def test_a_temporal_pick_owned_by_another_user_is_refused_with_the_fact_but_identifier_links_stay() -> None:
    c = closure()
    store = FactStore(
        [
            fact("e1:1", "search_1", {"b@x.com"}),  # the decoy row's record belongs to user_2
            fact("e2:1", "export_7", {"a@x.com"}, via="x"),  # a real identifier link for user_1
        ]
    )
    lineage = LineageGraph([delete_row("activity_1", "search_1", 5)])
    accepted, rejected = ResourceAttribution.for_case_detailed(case("case_1", "a@x.com"), "user_1", c, store, lineage)
    assert "search_1" not in {a.resource_id for a in accepted}
    assert "export_7" in {a.resource_id for a in accepted}
    refused = [r for r in rejected if r.resource_id == "search_1"]
    assert len(refused) == 1 and refused[0].tier is LinkTier.ACTIVITY_TEMPORAL
    assert refused[0].conflict_fact == "e1:1" and refused[0].conflict_user == "user_2"
    # the same row is the owner's own pick for user_2
    accepted2, _ = ResourceAttribution.for_case_detailed(case("case_2", "b@x.com"), "user_2", c, store, lineage)
    assert "search_1" in {a.resource_id for a in accepted2}


def test_a_resource_picked_for_two_different_subjects_is_found_and_one_subject_twice_is_not() -> None:
    c = closure()
    store = FactStore([fact("e1:1", "export_9", {"c@x.com"}, via="x")])  # no conflicting owner for search_1
    lineage = LineageGraph([delete_row("activity_1", "search_1", 5)])
    cases = [case("case_1", "a@x.com"), case("case_2", "b@x.com"), case("case_3", "a@x.com")]
    none = SimpleNamespace(assigned={})
    shared = find_shared_picks(cases, c, store, lineage, (none,))  # type: ignore[arg-type]
    assert shared == {"search_1": ("case_1", "case_2")}  # case_3 is user_1 again: not a second subject
    only_one = find_shared_picks([case("case_1", "a@x.com"), case("case_3", "a@x.com")], c, store, lineage, (none,))  # type: ignore[arg-type]
    assert only_one == {}


def test_joint_picks_count_toward_sharing() -> None:
    c = closure()
    store = FactStore([])
    lineage = LineageGraph([])
    joint_pick: Any = SimpleNamespace(resource_id="account_5", tier=LinkTier.ACTIVITY_JOINT)
    joint = SimpleNamespace(assigned={"case_1": joint_pick, "case_2": joint_pick})
    shared = find_shared_picks([case("case_1", "a@x.com"), case("case_2", "b@x.com")], c, store, lineage, (joint,))  # type: ignore[arg-type]
    assert shared == {"account_5": ("case_1", "case_2")}
