"""Provider-agnostic LLM client: Gemini first, then Groq, then cache only.

Modes:
  deterministic  never calls a provider; a cached answer is still returned.
  live           cache, then each provider in order; answers are cached.
  replay         cache only; a miss raises CacheMiss.

Providers wrap the official SDKs (google-genai, groq), imported lazily so the
deterministic path needs neither. Keys come from the environment only.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from pcda.llm.cache import DiskCache, content_key
from pcda.llm.ledger import TokenLedger


class RunMode(str, Enum):
    DETERMINISTIC = "deterministic"
    LIVE = "live"
    REPLAY = "replay"


@dataclass(frozen=True)
class Attachment:
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class LLMRequest:
    task: str  # e.g. "intake", "perception.image", "category"
    prompt: str
    schema: dict[str, Any] | None = None  # JSON schema the answer must satisfy
    attachments: tuple[Attachment, ...] = ()

    def cache_key(self) -> str:
        return content_key(self.task, self.prompt, self.schema, [a.data for a in self.attachments])


@dataclass(frozen=True)
class LLMResponse:
    text: str
    parsed: Any
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False


class LLMUnavailable(RuntimeError):
    """Deterministic mode, no cached answer."""


class CacheMiss(RuntimeError):
    """Replay mode, no cached answer."""


@dataclass
class ProviderError(Exception):
    provider: str
    message: str
    retryable: bool = False
    retry_after: float | None = None  # seconds, from a Retry-After header when given

    def __str__(self) -> str:
        return f"{self.provider}: {self.message}"


class Provider(Protocol):
    name: str
    model: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self._api_key = os.environ.get("GEMINI_API_KEY")
        self._sdk: Any = None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _client(self) -> Any:
        if self._sdk is None:
            from google import genai

            self._sdk = genai.Client(api_key=self._api_key)
        return self._sdk

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError("llm.GeminiProvider.complete")


class GroqProvider:
    name = "groq"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        self._api_key = os.environ.get("GROQ_API_KEY")
        self._sdk: Any = None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _client(self) -> Any:
        if self._sdk is None:
            from groq import Groq

            self._sdk = Groq(api_key=self._api_key)
        return self._sdk

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError("llm.GroqProvider.complete")


@dataclass
class LLMClient:
    mode: RunMode
    cache: DiskCache
    ledger: TokenLedger
    providers: list[Provider] = field(default_factory=list)
    max_attempts: int = 3
    base_backoff: float = 2.0

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = request.cache_key()
        hit = self.cache.get(key)
        if hit is not None:
            self.ledger.record_cache_hit(hit["provider"], hit["model"])
            return LLMResponse(
                text=hit["text"],
                parsed=hit.get("parsed"),
                provider=hit["provider"],
                model=hit["model"],
                cached=True,
            )
        if self.mode is RunMode.DETERMINISTIC:
            raise LLMUnavailable(f"{request.task}: no cached answer in deterministic mode")
        if self.mode is RunMode.REPLAY:
            raise CacheMiss(f"{request.task}: cache miss for {key}")

        errors: list[str] = []
        for provider in self.providers:
            try:
                resp = self._with_retries(provider, request)
            except ProviderError as exc:
                errors.append(str(exc))
                continue
            self.ledger.record(resp.provider, resp.model, resp.input_tokens, resp.output_tokens)
            self.cache.put(
                key,
                {
                    "task": request.task,
                    "text": resp.text,
                    "parsed": resp.parsed,
                    "provider": resp.provider,
                    "model": resp.model,
                },
            )
            return resp
        raise LLMUnavailable(f"{request.task}: all providers failed: {'; '.join(errors) or 'none configured'}")

    def _with_retries(self, provider: Provider, request: LLMRequest) -> LLMResponse:
        last: ProviderError | None = None
        for attempt in range(self.max_attempts):
            try:
                return provider.complete(request)
            except ProviderError as exc:
                last = exc
                if not exc.retryable or attempt == self.max_attempts - 1:
                    raise
                delay = exc.retry_after if exc.retry_after is not None else self.base_backoff * 2**attempt
                time.sleep(delay)
        assert last is not None
        raise last


def build_client(mode: RunMode, cache: DiskCache, ledger: TokenLedger) -> LLMClient:
    providers: list[Provider] = []
    if mode is RunMode.LIVE:
        for p in (GeminiProvider(), GroqProvider()):
            if p.available:
                providers.append(p)
    return LLMClient(mode=mode, cache=cache, ledger=ledger, providers=providers)
