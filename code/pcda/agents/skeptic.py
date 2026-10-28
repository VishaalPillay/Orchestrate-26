"""Skeptic agent: challenges low-confidence decisions with evidence. Advisory only.

The one agentic loop in the system. For each target of a risk-ordered queue a
tool-using model reads the stores (agents/skeptic_tools.py) and answers
CONFIRM, CHALLENGE or PROPOSE. It can never write a step, a status or a
citation. Its answer is a claim that agents/skeptic_gate.py checks against the
stores; only a claim that passes becomes an Adjustment, and the solver then
solves the case again. Hallucinated ids are counted, not trusted.

NullSkepticAgent (deterministic mode) raises nothing; the pipeline flags
activity picks `skeptic_pending` there.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from pcda import config
from pcda.agents import prompts
from pcda.agents.skeptic_gate import GateResult, evaluate
from pcda.agents.skeptic_tools import SUBMIT_TOOL, TOOL_SCHEMAS, SkepticTools, SkepticWorld, compact
from pcda.identity import AttributedResource
from pcda.llm.client import CacheMiss, LLMClient, LLMUnavailable
from pcda.solver import Certificate

SKEPTIC_TASK: Final = "skeptic"
SKEPTIC_MODEL_ENV: Final = "SKEPTIC_MODEL"
MAX_CALLS_PER_TARGET: Final = config.SKEPTIC_MAX_CALLS_PER_TARGET
MAX_GATE_RETRIES: Final = config.SKEPTIC_MAX_GATE_RETRIES
VERDICTS: Final = ("CONFIRM", "CHALLENGE", "PROPOSE")
KINDS: Final = ("OTHER_SUBJECT", "OTHER_REQUEST", "MISSED_LINK")


@dataclass(frozen=True)
class Objection:
    case_id: str
    target: str  # resource_id or obligation reference
    kind: str  # e.g. "WEAK_ATTRIBUTION", "MISSING_COPY", "TEMPORAL_DOUBT"
    message: str


class SkepticAgent(Protocol):
    def challenge(
        self, certificate: Certificate, attributions: list[AttributedResource]
    ) -> list[Objection]: ...


class NullSkepticAgent:
    """Deterministic mode: raises no objection. Attributions that need the
    skeptic are flagged by the pipeline instead (skeptic_pending)."""

    def challenge(
        self, certificate: Certificate, attributions: list[AttributedResource]
    ) -> list[Objection]:
        return []


@dataclass(frozen=True)
class Target:
    case_id: str
    kind: str  # PICK | FALLBACK | SINGLE_FACT | CONTESTED | FLAG
    resource_id: str | None
    priority: int  # lower first
    risk: float  # higher first inside a priority
    why: str

    @property
    def key(self) -> str:
        return f"{self.case_id}:{self.resource_id or '-'}"


@dataclass(frozen=True)
class Answer:
    verdict: str
    kind: str | None = None
    resource_id: str | None = None
    fact_id: str | None = None
    other_case_id: str | None = None
    reason: str = ""


@dataclass
class Adjustments:
    """What the gate accepted for one case."""

    remove: set[str] = field(default_factory=set)
    add: list[AttributedResource] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.remove or self.add)


@dataclass
class Investigation:
    target: Target
    answer: Answer | None
    gate: GateResult | None
    calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    models: list[str] = field(default_factory=list)
    tool_calls: int = 0
    cited_unseen: bool = False  # the cited id exists but no tool returned it
    gate_retries: int = 0  # rejections returned to the agent
    outcome: str = ""  # CONFIRM | CHALLENGE | PROPOSE | NO_ANSWER | UNFINISHED | ERROR

    def record(self) -> dict[str, Any]:
        t, a, g = self.target, self.answer, self.gate
        return {
            "case_id": t.case_id,
            "target_kind": t.kind,
            "resource_id": t.resource_id,
            "priority": t.priority,
            "why": t.why,
            "outcome": self.outcome,
            "answer": None
            if a is None
            else {
                "verdict": a.verdict, "kind": a.kind, "resource_id": a.resource_id,
                "fact_id": a.fact_id, "other_case_id": a.other_case_id, "reason": a.reason[:300],
            },
            "gate": None
            if g is None or a is None or a.verdict == "CONFIRM"
            else {
                "accepted": g.accepted, "action": g.action, "checks": list(g.checks),
                "reject_reason": g.reject_reason, "hallucinated": g.hallucinated,
            },
            "cited_unseen": self.cited_unseen,
            "gate_retries": self.gate_retries,
            "calls": self.calls,
            "cached_calls": self.cached_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "models": sorted(set(self.models)),
        }


class Budget:
    """A hard global cap on model calls. A cache hit costs a call, so live and replay walk the same queue."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    @property
    def left(self) -> int:
        return self.limit - self.used

    def spend(self) -> None:
        self.used += 1


class LLMSkepticAgent:
    def __init__(self, client: LLMClient, world: SkepticWorld) -> None:
        self._client = client
        self._world = world

    def investigate(self, target: Target, budget: Budget) -> Investigation:
        """At most MAX_CALLS_PER_TARGET model calls; stops early when the global budget runs out."""
        world = self._world
        tools = SkepticTools(world, target.case_id)
        system, user = prompts.load("skeptic.investigate")
        user = (
            user.replace("{{TARGET}}", _describe(target))
            .replace("{{CONTEXT}}", compact(tools.case_context(target.case_id), 1800))
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        inv = Investigation(target, None, None)
        nudged = False
        for turn in range(1, MAX_CALLS_PER_TARGET + 1):
            if budget.left <= 0:
                inv.outcome = "UNFINISHED"
                return inv
            last = turn == MAX_CALLS_PER_TARGET
            if last:
                messages.append({"role": "user", "content": "Last turn: call submit_verdict now."})
            try:
                resp = self._client.complete_turn(
                    SKEPTIC_TASK, SKEPTIC_MODEL_ENV, messages, [SUBMIT_TOOL] if last else TOOL_SCHEMAS + [SUBMIT_TOOL]
                )
            except (LLMUnavailable, CacheMiss) as exc:
                inv.outcome = "ERROR"
                inv.models.append(f"error:{str(exc)[:80]}")
                return inv
            budget.spend()
            inv.calls += 1
            inv.cached_calls += int(resp.cached)
            inv.input_tokens += resp.input_tokens
            inv.output_tokens += resp.output_tokens
            inv.models.append(f"{resp.provider}/{resp.model}")
            messages.append(resp.message)
            calls = resp.message.get("tool_calls") or []
            answer: Answer | None = None
            submitted = [c for c in calls if c["function"]["name"] == "submit_verdict"]
            if submitted:
                answer = _parse_answer(submitted[0]["function"].get("arguments", ""))
                if answer is None and not last:
                    for c in calls:
                        messages.append(
                            {"role": "tool", "tool_call_id": c["id"], "content": '{"error":"invalid verdict; resubmit"}'}
                        )
                    continue
            elif calls and not last:
                for c in calls:
                    inv.tool_calls += 1
                    out = tools.call(c["function"]["name"], c["function"].get("arguments", "{}"))
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": out})
                continue
            else:
                answer = _parse_answer(str(resp.message.get("content") or ""))
            if answer is None:
                if nudged or last:
                    inv.outcome = "NO_ANSWER"
                    return inv
                nudged = True
                messages.append(
                    {
                        "role": "user",
                        "content": 'Reply with one JSON object only: {"verdict": "CONFIRM|CHALLENGE|PROPOSE", '
                        '"kind": null, "resource_id": null, "fact_id": null, "other_case_id": null, "reason": "..."}',
                    }
                )
                continue
            inv.answer = answer
            inv.outcome = answer.verdict
            inv.gate = evaluate(answer, target, world)
            if answer.fact_id and not inv.gate.hallucinated and answer.fact_id not in tools.seen:
                inv.cited_unseen = True
            if answer.verdict != "CONFIRM" and not inv.gate.accepted and inv.gate_retries < MAX_GATE_RETRIES and not last:
                # Tell the agent which check failed and let it revise; a CONFIRM never reaches here,
                # so recorded CONFIRM conversations replay unchanged.
                inv.gate_retries += 1
                feedback = gate_feedback(inv.gate, MAX_GATE_RETRIES - inv.gate_retries)
                if submitted:
                    for c in calls:
                        content = feedback if c is submitted[0] else '{"error":"ignored: a verdict was submitted"}'
                        messages.append({"role": "tool", "tool_call_id": c["id"], "content": content})
                else:
                    messages.append({"role": "user", "content": feedback})
                continue
            # Accepted, a CONFIRM, or out of retries: a rejected claim changes nothing.
            return inv
        inv.outcome = "NO_ANSWER"
        return inv


def gate_feedback(gate: GateResult, retries_left: int) -> str:
    """The gate's structured rejection, returned to the agent as a tool result."""
    return json.dumps(
        {
            "gate": "rejected",
            "failed_check": gate.reject_reason,
            "passed_checks": list(gate.checks),
            "cited": gate.cited,
            "hallucinated": gate.hallucinated,
            "retries_left": retries_left,
            "next": "cite an id that passes the failed check, or submit_verdict CONFIRM",
        },
        sort_keys=True,
    )


def _describe(t: Target) -> str:
    lines = [f"case: {t.case_id}", f"decision under review: {t.kind}", f"why it is low-confidence: {t.why}"]
    if t.resource_id:
        lines.append(f"resource: {t.resource_id}")
    return "\n".join(lines)


def _parse_answer(text: str) -> Answer | None:
    body = text.strip()
    if "{" not in body or "}" not in body:
        return None
    try:
        data = json.loads(body[body.index("{") : body.rindex("}") + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("verdict") not in VERDICTS:
        return None
    kind = data.get("kind")
    if kind not in KINDS:
        kind = None

    def text_of(key: str) -> str | None:
        v = data.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else None

    return Answer(
        verdict=data["verdict"],
        kind=kind,
        resource_id=text_of("resource_id"),
        fact_id=text_of("fact_id"),
        other_case_id=text_of("other_case_id"),
        reason=str(data.get("reason") or "")[:400],
    )


def run_queue(
    agent: LLMSkepticAgent, queue: list[Target], budget: Budget
) -> tuple[dict[str, Adjustments], list[Investigation], int]:
    """Investigate targets in order until the budget is spent. Returns (adjustments by case,
    investigations, number of queued targets never reached). An unfinished target is discarded."""
    adjustments: dict[str, Adjustments] = {}
    done: list[Investigation] = []
    unreached = 0
    failures = 0
    for i, target in enumerate(queue):
        if budget.left <= 0:
            unreached = len(queue) - i
            break
        inv = agent.investigate(target, budget)
        done.append(inv)
        failures = failures + 1 if inv.outcome == "ERROR" else 0
        if failures >= 3:  # every provider is failing: stop instead of burning the queue
            unreached = len(queue) - i - 1
            break
        if inv.outcome == "UNFINISHED":
            unreached = len(queue) - i
            break
        g = inv.gate
        if g is not None and g.accepted and g.resource_id:
            adj = adjustments.setdefault(target.case_id, Adjustments())
            if g.action == "REMOVE":
                adj.remove.add(g.resource_id)
            elif g.action == "ADD" and g.added is not None:
                adj.add.append(g.added)
    return adjustments, done, unreached
