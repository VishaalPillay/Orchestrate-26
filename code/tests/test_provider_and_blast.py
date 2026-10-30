"""Fake-client tests for two failure handlers listed in the robustness table: the HTTP 400 reasoning_effort retry
and the skeptic blast-radius stop."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import openai

from pcda import config
from pcda.agents.skeptic_run import Blast, exceeds_blast_limit
from pcda.llm.client import OpenAICompatProvider


class FakeCompletions:
    """chat.completions.create: rejects reasoning_effort="none" with HTTP 400, accepts anything else."""

    def __init__(self) -> None:
        self.efforts: list[str | None] = []

    def create(self, **kwargs: Any) -> Any:
        self.efforts.append(kwargs.get("reasoning_effort"))
        if kwargs.get("reasoning_effort") == "none":
            response = httpx.Response(400, request=httpx.Request("POST", "http://fake"), json={"error": "bad effort"})
            raise openai.BadRequestError("reasoning_effort none is not supported", response=response, body=None)
        message = SimpleNamespace(content='{"ok": true}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2))


def provider(fake: FakeCompletions) -> OpenAICompatProvider:
    p = OpenAICompatProvider("gemini", "http://fake", "key", rpm=10_000, disable_thinking=True)
    p._sdk = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return p


def test_http_400_on_reasoning_effort_none_retries_once_at_low_and_remembers() -> None:
    fake = FakeCompletions()
    p = provider(fake)
    text, tokens_in, tokens_out = p.chat("flash-lite", [{"role": "user", "content": "x"}], json_mode=True)
    assert (text, tokens_in, tokens_out) == ('{"ok": true}', 3, 2)
    assert fake.efforts == ["none", "low"]  # one rejected call, one retry
    p.chat("flash-lite", [{"role": "user", "content": "x"}], json_mode=True)
    assert fake.efforts == ["none", "low", "low"]  # remembered for that model: no second rejection
    p.chat("other-model", [{"role": "user", "content": "x"}], json_mode=True)
    assert fake.efforts[-2:] == ["none", "low"]  # another model is tried at "none" first


def test_a_400_that_is_not_about_reasoning_effort_is_not_retried() -> None:
    class Always400(FakeCompletions):
        def create(self, **kwargs: Any) -> Any:
            self.efforts.append(kwargs.get("reasoning_effort"))
            response = httpx.Response(400, request=httpx.Request("POST", "http://fake"))
            raise openai.BadRequestError("bad", response=response, body=None)

    fake = Always400()
    p = OpenAICompatProvider("gemini", "http://fake", "key", rpm=10_000, disable_thinking=False)  # no effort set
    p._sdk = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    try:
        p.chat("m", [{"role": "user", "content": "x"}], json_mode=False)
    except Exception as exc:
        assert "HTTP 400" in str(exc)
    else:
        raise AssertionError("a 400 must surface")
    assert fake.efforts == [None]  # a single call


def blast(verdicts: int, steps: int) -> Blast:
    return Blast(tuple((f"case_{i}", "INCOMPLETE", "COMPLETE") for i in range(verdicts)), steps, ())


def test_blast_radius_stop_triggers_above_either_limit_and_not_at_it() -> None:
    v, s = config.BLAST_MAX_VERDICTS, config.BLAST_MAX_STEPS
    assert not exceeds_blast_limit(blast(0, 0), v, s)
    assert not exceeds_blast_limit(blast(v, s), v, s)  # at the limit: allowed
    assert exceeds_blast_limit(blast(v + 1, 0), v, s)
    assert exceeds_blast_limit(blast(0, s + 1), v, s)
