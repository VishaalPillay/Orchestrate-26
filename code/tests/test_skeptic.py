"""Skeptic: tools stay inside the stores and under their cap, the gate accepts only checked claims,
the loop respects its budgets, and a recorded conversation replays without a provider."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from pcda.agents.skeptic import Answer, Budget, LLMSkepticAgent, Target, _parse_answer, run_queue
from pcda.agents.skeptic_gate import evaluate
from pcda.agents.skeptic_tools import TOOL_CAP, SkepticTools, SkepticWorld, compact, mentions
from pcda.cases import CaseRequest
from pcda.facts import Fact, FactKind, FactStore, parse_ts
from pcda.identity import AttributedResource, IdentityClosure, LinkTier, UnionFind
from pcda.lineage import Claim, LineageGraph, SystemVote
from pcda.llm.cache import DiskCache
from pcda.llm.client import CacheMiss, LLMClient, RunMode
from pcda.llm.ledger import TokenLedger

T0 = parse_ts("2026-03-01T08:00:00Z")


def ts(days: float) -> Any:
    return T0 + timedelta(days=days)


def make_closure() -> IdentityClosure:
    uf = UnionFind()
    users = {"a@x.com": "user_1", "b@x.com": "user_2"}
    for ident, user in users.items():
        uf.union(user, ident)
    return IdentityClosure(uf, dict(users, user_1="user_1", user_2="user_2"))


def fact(fid: str, rid: str, idents: set[str], at: float = 0.0, **attrs: str) -> Fact:
    return Fact(
        fact_id=fid, evidence_id=fid.split(":")[0], captured_at=ts(at), observed_at=ts(at), system_id="account_service",
        resource_id=rid, category=None, kind=FactKind.PRESENCE, identifiers=frozenset(idents), attrs=attrs,
    )


def claim(aid: str, rid: str, minutes: float, result: str = "SUCCESS", event: str = "DELETE_RECORD", details: str = "") -> Claim:
    return Claim(aid, T0 + timedelta(minutes=minutes), event, "deletion_worker", "account_service", rid, result, details)


def case(cid: str, status: str = "COMPLETE", reported_min: float = 0.0) -> CaseRequest:
    return CaseRequest(cid, "a@x.com", ts(-1), status, T0 + timedelta(minutes=reported_min), ts(5), "delete me")


def pick(rid: str, tier: LinkTier = LinkTier.ACTIVITY_JOINT, system: str = "account_service") -> AttributedResource:
    return AttributedResource(rid, system, tier, ("r",), ("activity_1",), needs_skeptic=True)


def result(subject: str, attributions: list[AttributedResource]) -> Any:
    cert = SimpleNamespace(subject=subject, obligations=(), flags=(), verdict=SimpleNamespace(value="COMPLETE"))
    return SimpleNamespace(certificate=cert, attributions=tuple(attributions))


def make_world(facts: list[Fact], claims: list[Claim], results: dict[str, Any], cases: list[CaseRequest]) -> SkepticWorld:
    return SkepticWorld(
        store=FactStore(facts),
        lineage=LineageGraph(claims),
        closure=make_closure(),
        templates={},
        cases={c.case_id: c for c in cases},
        results=results,
        canonical={"account_": SystemVote("account_", "account_service", "catalog", {}, {})},
    )


def base_world() -> SkepticWorld:
    facts = [
        fact("evidence_1:00001", "account_900", {"b@x.com"}, at=-0.5),  # account_900 belongs to user_2
        fact("evidence_2:00001", "search_5", {"a@x.com"}, at=-0.2, note="source account_77 tombstone"),
        fact("evidence_3:00001", "account_88", {"a@x.com"}, at=-30),  # stale: before requested_at
    ]
    claims = [
        claim("activity_1", "account_900", 5),
        claim("activity_2", "account_55", 3, "PARTIAL"),
        claim("activity_3", "account_77", 1),
        claim("activity_4", "account_88", 1),
    ]
    results = {
        "case_1": result("user_1", [pick("account_900"), pick("account_55", LinkTier.ACTIVITY_TEMPORAL)]),
        "case_2": result("user_2", [AttributedResource("account_9", "account_service", LinkTier.LOG_TRACE, (), ())]),
    }
    return make_world(facts, claims, results, [case("case_1", reported_min=5), case("case_2", "PARTIALLY_COMPLETE", 3)])


TARGET = Target("case_1", "PICK", "account_900", 0, 1.0, "test")


def test_compact_respects_the_cap_and_says_what_it_dropped() -> None:
    out = compact({"items": [{"k": "x" * 200} for _ in range(40)]})
    assert len(out) <= TOOL_CAP
    assert json.loads(out)["truncated"] > 0


def test_mentions_is_whole_token() -> None:
    assert mentions("see account_77 now", "account_77")
    assert not mentions("see account_770", "account_77")
    assert not mentions("xaccount_77", "account_77")


def test_tools_show_owners_never_raw_identifiers_and_only_store_content() -> None:
    tools = SkepticTools(base_world(), "case_1")
    out = tools.call("find_facts", json.dumps({"identifier": "B@X.COM"}))
    assert "b@x.com" not in out.lower()
    data = json.loads(out)
    assert data["facts"][0]["owners"] == ["user_2"] and data["facts"][0]["resource_id"] == "account_900"
    assert json.loads(tools.call("resource_timeline", '{"resource_id": "account_404"}')) == {"error": "not_found"}
    assert json.loads(tools.call("nope", "{}"))["error"] == "unknown_tool"
    assert json.loads(tools.call("find_facts", "not json"))["error"] == "bad_arguments"
    cand = json.loads(tools.call("candidate_resources", '{"case_id": "case_1", "system_id": "account_service"}'))
    assert next(r["resource_id"] for r in cand["rows"]) == "account_900"  # nearest to reported_at first
    assert "activity_1" in tools.seen


def test_confirm_changes_nothing() -> None:
    assert not evaluate(Answer("CONFIRM"), TARGET, base_world()).accepted


def test_hallucinated_id_is_rejected_and_counted() -> None:
    g = evaluate(Answer("CHALLENGE", "OTHER_SUBJECT", "account_900", "evidence_99:00009"), TARGET, base_world())
    assert not g.accepted and g.hallucinated and g.reject_reason == "cited_id_not_found"


def test_challenge_other_subject_accepted_only_when_fact_links_to_another_user() -> None:
    w = base_world()
    ok = evaluate(Answer("CHALLENGE", "OTHER_SUBJECT", "account_900", "evidence_1:00001"), TARGET, w)
    assert ok.accepted and ok.action == "REMOVE" and ok.resource_id == "account_900"
    assert any(c.startswith("link_resolves_to_other_user:user_2") for c in ok.checks)
    # a fact that belongs to the subject cannot challenge the subject's own pick
    bad = evaluate(Answer("CHALLENGE", "OTHER_SUBJECT", "account_900", "evidence_3:00001"), TARGET, w)
    assert not bad.accepted and bad.reject_reason == "fact_does_not_link_resource"
    # a resource that is not an activity pick cannot be challenged
    strong = Target("case_2", "PICK", "account_9", 0, 1.0, "t")
    assert evaluate(Answer("CHALLENGE", "OTHER_SUBJECT", "account_9", "evidence_1:00001"), strong, w).reject_reason == (
        "not_a_challengeable_pick"
    )


def test_challenge_other_request_needs_a_closer_row_and_no_fact_based_link() -> None:
    w = base_world()
    # activity_2 (account_55, PARTIAL, 3 min after case_1 started) fits case_2 (reported at 3 min, PARTIALLY_COMPLETE)
    t = Target("case_1", "PICK", "account_55", 0, 1.0, "t")
    w.cases["case_1"] = case("case_1", reported_min=3)  # the row is just as close to case_1
    g = evaluate(Answer("CHALLENGE", "OTHER_REQUEST", "account_55", "activity_2", "case_2"), t, w)
    assert not g.accepted and g.reject_reason.startswith("row_not_closer")
    w.cases["case_1"] = case("case_1", reported_min=60)
    g = evaluate(Answer("CHALLENGE", "OTHER_REQUEST", "account_55", "activity_2", "case_2"), t, w)
    assert not g.accepted and g.reject_reason == "other_request_holds_account_9_at_LOG_TRACE"
    w.results["case_2"] = result("user_2", [])
    g = evaluate(Answer("CHALLENGE", "OTHER_REQUEST", "account_55", "activity_2", "case_2"), t, w)
    assert g.accepted and g.action == "REMOVE"


def test_propose_adds_a_link_only_through_a_checked_co_mention() -> None:
    w = base_world()
    g = evaluate(Answer("PROPOSE", "MISSED_LINK", "account_77", "evidence_2:00001"), TARGET, w)
    assert g.accepted and g.action == "ADD" and g.added is not None
    assert g.added.tier is LinkTier.SKEPTIC and g.added.system_id == "account_service"
    assert g.added.evidence_ids == ("evidence_2",) and not g.added.needs_skeptic
    # not mentioned by the cited fact
    assert evaluate(Answer("PROPOSE", "MISSED_LINK", "account_66", "evidence_2:00001"), TARGET, w).reject_reason == (
        "fact_does_not_mention_resource"
    )
    # the fact belongs to another user
    assert evaluate(Answer("PROPOSE", "MISSED_LINK", "account_900", "evidence_1:00001"), TARGET, w).reject_reason.startswith(
        "fact_identifiers_resolve_to:user_2"
    )
    # a stale observation (before requested_at) is the case_17 decoy: rejected
    assert evaluate(Answer("PROPOSE", "MISSED_LINK", "account_88", "evidence_3:00001"), TARGET, w).reject_reason == (
        "stale_before_request"
    )
    # an activity row is not a fact
    assert evaluate(Answer("PROPOSE", "MISSED_LINK", "account_77", "activity_3"), TARGET, w).reject_reason == (
        "proposal_cites_no_fact"
    )


def test_parse_answer_is_strict_about_the_verdict() -> None:
    a = _parse_answer('```json\n{"verdict": "CHALLENGE", "kind": "OTHER_SUBJECT", "fact_id": "e:1", "reason": "r"}\n```')
    assert a is not None and a.verdict == "CHALLENGE" and a.kind == "OTHER_SUBJECT" and a.fact_id == "e:1"
    assert _parse_answer('{"verdict": "DELETE_EVERYTHING"}') is None
    assert _parse_answer("no json here") is None


class ScriptedProvider:
    """Plays a fixed list of assistant messages, one per call."""

    name = "scripted"

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = list(script)
        self.calls = 0
        self.seen: list[list[dict[str, Any]]] = []  # the messages of each call

    def model_for(self, request: Any) -> str:
        return "scripted-model"

    def chat(self, model: str, messages: Any, json_mode: bool) -> Any:  # pragma: no cover - unused
        raise AssertionError

    def chat_tools(self, model: str, messages: Any, tools: Any) -> Any:
        self.calls += 1
        self.seen.append(json.loads(json.dumps(messages)))
        return self.script.pop(0), 10, 5


def tool_call(name: str, args: dict[str, Any], cid: str = "c1") -> dict[str, Any]:
    return {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
    }


def final(**answer: Any) -> dict[str, Any]:
    return {"role": "assistant", "content": json.dumps(answer)}


def client(tmp_path: Any, script: list[dict[str, Any]], mode: RunMode = RunMode.LIVE) -> tuple[LLMClient, ScriptedProvider]:
    provider = ScriptedProvider(script)
    return LLMClient(mode, DiskCache(tmp_path), TokenLedger(None), [provider] if mode is RunMode.LIVE else []), provider


def test_loop_investigates_gates_and_replays_from_the_cache(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("SKEPTIC_MODEL", "scripted-model")
    w = base_world()
    script = [
        tool_call("resource_timeline", {"resource_id": "account_900"}),
        final(verdict="CHALLENGE", kind="OTHER_SUBJECT", resource_id="account_900", fact_id="evidence_1:00001", reason="x"),
    ]
    live, provider = client(tmp_path, script)
    inv = LLMSkepticAgent(live, w).investigate(TARGET, Budget(10))
    assert inv.outcome == "CHALLENGE" and inv.gate is not None and inv.gate.accepted
    assert inv.calls == 2 and inv.tool_calls == 1 and provider.calls == 2
    assert not inv.cited_unseen  # resource_timeline returned that fact
    replay, _ = client(tmp_path, [], RunMode.REPLAY)
    again = LLMSkepticAgent(replay, w).investigate(TARGET, Budget(10))
    assert again.record()["answer"] == inv.record()["answer"] and again.cached_calls == 2


def submit(**answer: Any) -> dict[str, Any]:
    return tool_call("submit_verdict", answer, "s1")


def test_submit_verdict_tool_ends_the_investigation_and_a_bad_one_is_resubmitted(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("SKEPTIC_MODEL", "scripted-model")
    script = [
        submit(verdict="MAYBE", reason="x"),
        submit(verdict="CHALLENGE", kind="OTHER_SUBJECT", resource_id="account_900", fact_id="evidence_1:00001", reason="x"),
    ]
    live, _provider = client(tmp_path, script)
    inv = LLMSkepticAgent(live, base_world()).investigate(TARGET, Budget(10))
    assert inv.outcome == "CHALLENGE" and inv.calls == 2 and inv.gate is not None and inv.gate.accepted


def test_unseen_citation_is_flagged_but_still_gated_on_the_stores(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("SKEPTIC_MODEL", "scripted-model")
    live, _ = client(
        tmp_path, [final(verdict="CHALLENGE", kind="OTHER_SUBJECT", resource_id="account_900", fact_id="evidence_1:00001", reason="x")]
    )
    inv = LLMSkepticAgent(live, base_world()).investigate(TARGET, Budget(10))
    assert inv.cited_unseen and inv.gate is not None and inv.gate.accepted


def test_a_tool_loop_without_an_answer_ends_in_no_answer_after_eight_calls(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("SKEPTIC_MODEL", "scripted-model")
    script = [tool_call("get_rule", {"category": "X"}, f"c{i}") for i in range(7)] + [{"role": "assistant", "content": "hmm"}]
    live, provider = client(tmp_path, script)
    inv = LLMSkepticAgent(live, base_world()).investigate(TARGET, Budget(100))
    assert inv.outcome == "NO_ANSWER" and inv.calls == 8 and provider.calls == 8 and inv.gate is None


def test_budget_stops_mid_target_and_discards_it(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("SKEPTIC_MODEL", "scripted-model")
    script = [tool_call("get_rule", {"category": "X"}), final(verdict="CHALLENGE", kind="OTHER_SUBJECT", resource_id="account_900",
                                                                fact_id="evidence_1:00001", reason="x")]
    live, provider = client(tmp_path, script)
    agent = LLMSkepticAgent(live, base_world())
    adjustments, done, unreached = run_queue(agent, [TARGET, Target("case_1", "PICK", "account_55", 3, 1.0, "t")], Budget(1))
    assert provider.calls == 1 and done[0].outcome == "UNFINISHED" and not adjustments and unreached == 2


def test_a_gate_rejection_is_returned_to_the_agent_which_may_revise(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("SKEPTIC_MODEL", "scripted-model")
    # evidence_3 is the subject's own fact: OTHER_SUBJECT fails; the revised claim cites evidence_1 (user_2).
    script = [
        submit(verdict="CHALLENGE", kind="OTHER_SUBJECT", resource_id="account_900", fact_id="evidence_3:00001", reason="x"),
        submit(verdict="CHALLENGE", kind="OTHER_SUBJECT", resource_id="account_900", fact_id="evidence_1:00001", reason="y"),
    ]
    live, provider = client(tmp_path, script)
    agent = LLMSkepticAgent(live, base_world())
    inv = agent.investigate(TARGET, Budget(10))
    assert provider.calls == 2 and inv.gate_retries == 1
    assert inv.gate is not None and inv.gate.accepted and inv.answer is not None and inv.answer.reason == "y"
    # The second call saw the structured rejection as the submit_verdict tool result.
    second = provider.seen[1]
    feedback = json.loads(next(m["content"] for m in second if m.get("role") == "tool"))
    assert feedback["gate"] == "rejected" and feedback["failed_check"] == "fact_does_not_link_resource"
    assert feedback["passed_checks"] == ["cited_id_exists", "resource_is_activity_pick"] and feedback["retries_left"] == 1


def test_after_two_rejected_retries_the_claim_falls_back_to_no_change(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("SKEPTIC_MODEL", "scripted-model")
    bad = dict(verdict="CHALLENGE", kind="OTHER_SUBJECT", resource_id="account_900", fact_id="evidence_3:00001", reason="x")
    live, provider = client(tmp_path, [submit(**bad), submit(**bad), submit(**bad), submit(**bad)])
    agent = LLMSkepticAgent(live, base_world())
    adjustments, done, _ = run_queue(agent, [TARGET], Budget(10))
    inv = done[0]
    assert provider.calls == 3 and inv.gate_retries == 2  # first answer + 2 retries, then stop
    assert inv.gate is not None and not inv.gate.accepted and adjustments == {}


def test_confirm_is_never_sent_back(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("SKEPTIC_MODEL", "scripted-model")
    live, provider = client(tmp_path, [submit(verdict="CONFIRM", reason="ok")])
    inv = LLMSkepticAgent(live, base_world()).investigate(TARGET, Budget(10))
    assert provider.calls == 1 and inv.gate_retries == 0 and inv.outcome == "CONFIRM"


def test_skeptic_budgets_come_from_config_env_then_overrides(monkeypatch: Any) -> None:
    from pcda import config

    monkeypatch.delenv("SKEPTIC_BUDGET_TEST", raising=False)
    monkeypatch.delenv("SKEPTIC_BUDGET_SAMPLE", raising=False)
    assert config.skeptic_budgets() == {"sample": 60, "test": 0}
    monkeypatch.setenv("SKEPTIC_BUDGET_TEST", "120")
    assert config.skeptic_budgets()["test"] == 120
    assert config.skeptic_budgets({"test": 5})["test"] == 5


def test_replay_of_an_unrecorded_conversation_is_a_cache_miss(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("SKEPTIC_MODEL", "scripted-model")
    replay, _ = client(tmp_path, [], RunMode.REPLAY)
    inv = LLMSkepticAgent(replay, base_world()).investigate(TARGET, Budget(5))
    assert inv.outcome == "ERROR"
    assert issubclass(CacheMiss, RuntimeError)
