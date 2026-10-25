"""LLM client (fake provider, no network) and image grounding (synthetic transcriptions)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcda.agents.perception import PerceivedRecord, PerceivedScreen, parse_screen
from pcda.facts import FactKind, parse_ts
from pcda.ingest.base import IngestReport, ManifestRow
from pcda.ingest.image_adapter import ground_screen
from pcda.llm.cache import DiskCache
from pcda.llm.client import (
    CacheMiss,
    LLMClient,
    LLMRequest,
    LLMUnavailable,
    ProviderError,
    RunMode,
)
from pcda.llm.ledger import TokenLedger


class FakeProvider:
    def __init__(self, name: str, replies: list[object]) -> None:
        self.name = name
        self.replies = list(replies)  # str answers or ProviderError to raise
        self.calls = 0

    def model_for(self, request: LLMRequest) -> str:
        return request.model

    def chat(self, model, messages, json_mode):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, ProviderError):
            raise reply
        return reply, 10, 5


@pytest.fixture(autouse=True)
def model_env(monkeypatch):
    monkeypatch.setenv("TEST_MODEL", "fake-model-1")


def request() -> LLMRequest:
    return LLMRequest(task="t", prompt="p", model_env="TEST_MODEL", schema={"type": "object", "required": ["records"]})


def client(tmp_path: Path, mode: RunMode, providers) -> LLMClient:
    ledger = TokenLedger(tmp_path / "run_log.jsonl")
    return LLMClient(mode, DiskCache(tmp_path / "llm"), ledger, providers, base_backoff=0.0)


def test_live_caches_and_deterministic_replay_reuse(tmp_path):
    p = FakeProvider("a", ['{"records": []}'])
    live = client(tmp_path, RunMode.LIVE, [p])
    assert live.complete(request()).parsed == {"records": []}
    for mode in (RunMode.DETERMINISTIC, RunMode.REPLAY):
        hit = client(tmp_path, mode, []).complete(request())
        assert hit.cached and hit.parsed == {"records": []}
    assert p.calls == 1
    assert (tmp_path / "run_log.jsonl").read_text(encoding="utf-8").count('"event": "call"') == 1


def test_modes_without_cache(tmp_path):
    with pytest.raises(LLMUnavailable):
        client(tmp_path, RunMode.DETERMINISTIC, [FakeProvider("a", [])]).complete(request())
    with pytest.raises(CacheMiss):
        client(tmp_path, RunMode.REPLAY, []).complete(request())


def test_cache_key_depends_on_model(tmp_path, monkeypatch):
    client(tmp_path, RunMode.LIVE, [FakeProvider("a", ['{"records": []}'])]).complete(request())
    monkeypatch.setenv("TEST_MODEL", "fake-model-2")
    with pytest.raises(CacheMiss):
        client(tmp_path, RunMode.REPLAY, []).complete(request())


def test_one_json_repair_then_fallback_provider(tmp_path):
    bad = FakeProvider("a", ["not json", '{"no_records": 1}'])
    good = FakeProvider("b", ['{"records": [1]}'])
    resp = client(tmp_path, RunMode.LIVE, [bad, good]).complete(request())
    assert bad.calls == 2 and resp.provider == "b" and resp.parsed == {"records": [1]}


def test_retry_honours_retry_after_and_caps_attempts(tmp_path):
    flaky = FakeProvider("a", [ProviderError("a", "429", True, 0.0), '{"records": []}'])
    assert client(tmp_path, RunMode.LIVE, [flaky]).complete(request()).parsed == {"records": []}
    dead = FakeProvider("a", [ProviderError("a", "429", True, 0.0)] * 3)
    with pytest.raises(LLMUnavailable):
        client(tmp_path / "fresh", RunMode.LIVE, [dead]).complete(request())  # empty cache
    assert dead.calls == 3


ROW = ManifestRow("evidence_09", "evidence/images/x.png", "test", parse_ts("2026-09-01T09:00:00Z"))
PREFIXES = frozenset({"search_", "data_flow_", "backup_", "account_"})


def ground(*records: PerceivedRecord) -> tuple[list, IngestReport]:
    report = IngestReport()
    return ground_screen(PerceivedScreen("evidence_09", "Search Index Inspector", records), ROW, report, PREFIXES), report


def test_grounding_drops_unknown_ids_and_normalizes_identifiers():
    facts, report = ground(
        PerceivedRecord("search_428", None, emails=("G.Silva76@Gmail.com",), user_ids=("user_13",)),
        PerceivedRecord("invoice_77", None, emails=("x@y.co",)),
        PerceivedRecord(None, None),
    )
    assert [f.resource_id for f in facts] == ["search_428"]
    assert facts[0].identifiers == {"g.silva76@gmail.com", "user_13"}
    assert facts[0].attrs["pii_fields"] == "email"  # user_13 is a reference, not PII
    assert facts[0].attrs["src"] == "perception"
    assert sum("dropped" in w for w in report.warnings) == 2


def test_observed_at_is_clamped_to_captured_at():
    before = PerceivedRecord("search_1", None, timestamps=(("2026-08-30 10:00", "2026-08-30T10:00:00Z"),))
    after = PerceivedRecord("search_2", None, timestamps=(("2026-09-05 10:00", "2026-09-05T10:00:00Z"),))
    invented_year = PerceivedRecord("search_3", None, timestamps=(("30 Aug 10:00", "2026-08-30T10:00:00Z"),))
    facts, _ = ground(before, after, invented_year)
    assert facts[0].observed_at == parse_ts("2026-08-30T10:00:00Z")
    assert facts[1].observed_at == ROW.captured_at
    assert facts[2].observed_at == ROW.captured_at


def test_state_words_map_through_fixed_vocabularies():
    facts, _ = ground(
        PerceivedRecord("data_flow_1121", None, states=("Continuous sync",)),
        PerceivedRecord("backup_9287", None, states=("Restore blocked",)),
        PerceivedRecord("account_5762", None, states=("Deleted",), emails=("a@b.co",)),
    )
    assert (facts[0].kind, facts[0].attrs["state"]) == (FactKind.FLOW_STATE, "ACTIVE")
    assert (facts[1].kind, facts[1].attrs["state"]) == (FactKind.RESTORE_STATE, "BLOCKED")
    assert facts[2].kind is FactKind.ABSENCE and "pii_fields" not in facts[2].attrs


def test_parse_screen_is_defensive():
    screen, notes = parse_screen("e", {"records": [{"resource_id": "search_1", "identifiers": "bad"}, "junk"]})
    assert [r.resource_id for r in screen.records] == ["search_1"] and notes


def test_misplaced_resource_id_is_adopted_only_when_unique():
    facts, report = ground(
        PerceivedRecord(None, None, emails=("sj.park@naver.com",), user_ids=("search_77",)),
        PerceivedRecord("CS-5034", None, fields={"Category": "search_88", "Other": "account_9"}),
    )
    assert [f.resource_id for f in facts] == ["search_77"]
    assert any("only id transcribed" in w for w in report.warnings)
    assert any("record 2 dropped" in w for w in report.warnings)  # two candidates: not guessed
