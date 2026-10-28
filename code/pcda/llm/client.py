"""Provider-agnostic LLM client over OpenAI-compatible endpoints.

Gemini and Groq both expose OpenAI-compatible chat endpoints, so one SDK
(openai) serves every provider; only base_url, key and model differ. The
provider chain is LLM_PROVIDERS (e.g. "gemini,groq"); provider NAME reads
NAME_BASE_URL and NAME_API_KEY. The model for a request comes from the env
var the request names (INTAKE_MODEL, PERCEPTION_MODEL, ...); a provider with
NAME_FALLBACK_MODEL set uses that model instead (Groq: GROQ_FALLBACK_MODEL).
Nothing is hardcoded and no key is ever printed or logged.

Modes:
  deterministic  never calls a provider; a cached answer is still returned.
  live           cache, then each provider in order; answers are cached.
  replay         cache only; a miss raises CacheMiss.

Per call: a client-side RPM limiter (LLM_RPM_LIMIT), thinking off when
LLM_DISABLE_THINKING=1, at most 3 attempts honoring Retry-After, strict JSON
with one repair retry, and a ledger row appended to code/.cache/runs/run_log.jsonl.
The cache key is the requested model + prompt + schema + attachment bytes.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final, Protocol

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
    model_env: str  # env var naming the model, e.g. "PERCEPTION_MODEL"
    system: str = ""
    schema: dict[str, Any] | None = None  # JSON schema the answer must satisfy
    attachments: tuple[Attachment, ...] = ()

    @property
    def model(self) -> str:
        model = os.environ.get(self.model_env, "").strip()
        if not model:
            raise LLMUnavailable(f"{self.task}: {self.model_env} is not set")
        return model

    def cache_key(self) -> str:
        return content_key(self.model, self.system + "\n\n" + self.prompt, self.schema, [a.data for a in self.attachments])


@dataclass(frozen=True)
class LLMResponse:
    text: str
    parsed: Any
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False


@dataclass(frozen=True)
class TurnResponse:
    message: dict[str, Any]  # assistant message: role, content, optional tool_calls
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False


class LLMUnavailable(RuntimeError):
    """Deterministic mode with no cached answer, or every provider failed."""


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


class InvalidJSON(ValueError):
    pass


class Provider(Protocol):
    name: str

    def model_for(self, request: LLMRequest) -> str: ...

    def chat(self, model: str, messages: list[dict[str, Any]], json_mode: bool) -> tuple[str, int, int]: ...

    def chat_tools(
        self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], int, int]: ...


class RateLimiter:
    """Sliding one-minute window shared by every thread using one provider."""

    def __init__(self, rpm: int) -> None:
        self._rpm = max(1, rpm)
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= 60.0:
                    self._calls.popleft()
                if len(self._calls) < self._rpm:
                    self._calls.append(now)
                    return
                wait = 60.0 - (now - self._calls[0])
            time.sleep(max(wait, 0.05))


# How each provider's OpenAI-compatible endpoint turns thinking off (or as low
# as it goes: gpt-oss on Groq cannot disable reasoning).
_THINKING_OFF: Final[dict[str, dict[str, Any]]] = {
    "gemini": {"reasoning_effort": "none"},
    "groq": {"reasoning_effort": "low"},
}


class OpenAICompatProvider:
    def __init__(self, name: str, base_url: str, api_key: str, rpm: int, disable_thinking: bool) -> None:
        self.name = name
        self._base_url = base_url
        self._api_key = api_key
        self._limiter = RateLimiter(rpm)
        self._extra = _THINKING_OFF.get(name, {}) if disable_thinking else {}
        self._low_thinking: set[str] = set()  # models that reject reasoning_effort="none"
        self._sdk: Any = None

    def model_for(self, request: LLMRequest) -> str:
        override = os.environ.get(f"{self.name.upper()}_FALLBACK_MODEL", "").strip()
        return override or request.model

    def _client(self) -> Any:
        if self._sdk is None:
            from openai import OpenAI  # lazy: deterministic runs never import it

            # Retries are ours (Retry-After aware), so the SDK's are off.
            timeout = float(os.environ.get("LLM_TIMEOUT_S", "120") or "120")
            self._sdk = OpenAI(base_url=self._base_url, api_key=self._api_key, max_retries=0, timeout=timeout)
        return self._sdk

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "[REDACTED]") if self._api_key else text

    def _create(self, kwargs: dict[str, Any]) -> Any:
        import openai

        self._limiter.acquire()
        kwargs.update(self._extra)
        if kwargs["model"] in self._low_thinking:
            kwargs["reasoning_effort"] = "low"
        try:
            try:
                return self._client().chat.completions.create(**kwargs)
            except openai.BadRequestError:
                # Some models (gemini-3.5-flash-lite) reject reasoning_effort="none" but
                # accept "low": retry once with the lowest effort they take, and remember it.
                if kwargs.get("reasoning_effort") != "none":
                    raise
                self._low_thinking.add(kwargs["model"])
                kwargs["reasoning_effort"] = "low"
                return self._client().chat.completions.create(**kwargs)
        except openai.APIStatusError as exc:
            retry_after = _retry_after(exc.response.headers if exc.response is not None else {})
            retryable = exc.status_code == 429 or exc.status_code >= 500
            raise ProviderError(self.name, self._redact(f"HTTP {exc.status_code}: {exc.message}"), retryable, retry_after) from exc
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise ProviderError(self.name, self._redact(f"{type(exc).__name__}: {exc}"), True, None) from exc

    def chat(self, model: str, messages: list[dict[str, Any]], json_mode: bool) -> tuple[str, int, int]:
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._create(kwargs)
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        return text, (usage.prompt_tokens if usage else 0), (usage.completion_tokens if usage else 0)

    def chat_tools(
        self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], int, int]:
        """One tool-calling turn. Returns the assistant message reduced to the fields
        that may be echoed back (role, content, tool_calls); Gemini's per-call
        extra_content (thought signatures) is kept for Gemini only."""
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [_for_provider(m, self.name) for m in messages],
            "temperature": 0,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = self._create(kwargs)
        usage = resp.usage
        return (
            _clean_assistant(resp.choices[0].message.model_dump(exclude_none=True), self.name),
            (usage.prompt_tokens if usage else 0),
            (usage.completion_tokens if usage else 0),
        )


def _clean_assistant(raw: dict[str, Any], provider: str) -> dict[str, Any]:
    """Only role, content and tool_calls survive, so a message from one provider can be sent to another."""
    out: dict[str, Any] = {"role": "assistant", "content": raw.get("content") or ""}
    calls = []
    for c in raw.get("tool_calls") or []:
        call: dict[str, Any] = {
            "id": c.get("id", ""),
            "type": "function",
            "function": {"name": c["function"]["name"], "arguments": c["function"].get("arguments") or "{}"},
        }
        if provider == "gemini" and c.get("extra_content"):
            call["extra_content"] = c["extra_content"]
        calls.append(call)
    if calls:
        out["tool_calls"] = calls
    return out


def _for_provider(message: dict[str, Any], provider: str) -> dict[str, Any]:
    """Drop another provider's private fields (extra_content) before sending a history."""
    if provider == "gemini" or not message.get("tool_calls"):
        return message
    calls = [{k: v for k, v in c.items() if k != "extra_content"} for c in message["tool_calls"]]
    return {**message, "tool_calls": calls}


def _retry_after(headers: Any) -> float | None:
    value = headers.get("retry-after") if headers else None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def parse_json(text: str, schema: dict[str, Any] | None) -> Any:
    """Strict JSON (a ```json fence is tolerated), checked against the schema's top level."""
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[4:] if body.lower().startswith("json") else body
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise InvalidJSON(f"not JSON: {exc}") from exc
    if schema:
        want = schema.get("type")
        if want == "object" and not isinstance(data, dict):
            raise InvalidJSON("top level is not an object")
        if want == "array" and not isinstance(data, list):
            raise InvalidJSON("top level is not an array")
        if isinstance(data, dict):
            missing = [k for k in schema.get("required", []) if k not in data]
            if missing:
                raise InvalidJSON(f"missing required keys {missing}")
    return data


def _messages(request: LLMRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if request.system:
        out.append({"role": "system", "content": request.system})
    user: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
    for a in request.attachments:
        b64 = base64.b64encode(a.data).decode("ascii")
        user.append({"type": "image_url", "image_url": {"url": f"data:{a.mime_type};base64,{b64}"}})
    out.append({"role": "user", "content": user})
    return out


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
            self.ledger.record_cache_hit(request.task, hit["provider"], hit["model"])
            return LLMResponse(
                text=hit["text"], parsed=hit.get("parsed"), provider=hit["provider"], model=hit["model"], cached=True
            )
        if self.mode is RunMode.DETERMINISTIC:
            raise LLMUnavailable(f"{request.task}: no cached answer in deterministic mode")
        if self.mode is RunMode.REPLAY:
            raise CacheMiss(f"{request.task}: cache miss for {key}")

        errors: list[str] = []
        for provider in self.providers:
            try:
                resp = self._ask(provider, request)
            except (ProviderError, InvalidJSON) as exc:
                errors.append(f"{provider.name}: {exc}")
                continue
            self.cache.put(
                key,
                {
                    "task": request.task,
                    "requested_model": request.model,
                    "text": resp.text,
                    "parsed": resp.parsed,
                    "provider": resp.provider,
                    "model": resp.model,
                    "input_tokens": resp.input_tokens,
                    "output_tokens": resp.output_tokens,
                },
            )
            return resp
        raise LLMUnavailable(f"{request.task}: all providers failed: {'; '.join(errors) or 'none configured'}")

    def complete_turn(
        self, task: str, model_env: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> TurnResponse:
        """One tool-calling turn of an agent loop, cached per turn.

        The key is the requested model, the whole message history and the tool
        schemas. Tool results are deterministic, so a replay walks the same
        path and every turn hits the cache; a miss in replay is an error.
        """
        model = LLMRequest(task, "", model_env).model
        key = content_key(model, json.dumps(messages, sort_keys=True, ensure_ascii=False), {"tools": tools}, [])
        hit = self.cache.get(key)
        if hit is not None:
            self.ledger.record_cache_hit(task, hit["provider"], hit["model"])
            return TurnResponse(hit["message"], hit["provider"], hit["model"], cached=True)
        if self.mode is RunMode.DETERMINISTIC:
            raise LLMUnavailable(f"{task}: no cached answer in deterministic mode")
        if self.mode is RunMode.REPLAY:
            raise CacheMiss(f"{task}: cache miss for {key}")
        errors: list[str] = []
        for provider in self.providers:
            pmodel = provider.model_for(LLMRequest(task, "", model_env))
            try:
                message, tin, tout = self._chat_tools_retrying(provider, pmodel, messages, tools, task)
            except ProviderError as exc:
                errors.append(f"{provider.name}: {exc}")
                continue
            self.cache.put(
                key,
                {"task": task, "requested_model": model, "message": message, "provider": provider.name,
                 "model": pmodel, "input_tokens": tin, "output_tokens": tout},
            )
            return TurnResponse(message, provider.name, pmodel, tin, tout)
        raise LLMUnavailable(f"{task}: all providers failed: {'; '.join(errors) or 'none configured'}")

    def _chat_tools_retrying(
        self, provider: Provider, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], task: str
    ) -> tuple[dict[str, Any], int, int]:
        for attempt in range(1, self.max_attempts + 1):
            try:
                message, tin, tout = provider.chat_tools(model, messages, tools)
            except ProviderError as exc:
                self.ledger.record_error(task, provider.name, model, attempt, exc.message)
                if not exc.retryable or attempt == self.max_attempts:
                    raise
                time.sleep(exc.retry_after if exc.retry_after is not None else self.base_backoff * 2 ** (attempt - 1))
                continue
            self.ledger.record(task, provider.name, model, tin, tout)
            return message, tin, tout
        raise AssertionError("unreachable")

    def _ask(self, provider: Provider, request: LLMRequest) -> LLMResponse:
        """One answer from one provider: retries, then strict JSON with one repair turn."""
        model = provider.model_for(request)
        messages = _messages(request)
        json_mode = request.schema is not None
        text, tin, tout = self._with_retries(provider, model, messages, json_mode, request.task)
        if not json_mode:
            return LLMResponse(text, None, provider.name, model, tin, tout)
        try:
            parsed = parse_json(text, request.schema)
        except InvalidJSON as exc:
            repair = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": f"Your reply was not valid for the required JSON format ({exc}). "
                    "Reply again with only the corrected JSON object and nothing else.",
                },
            ]
            text, rin, rout = self._with_retries(provider, model, repair, json_mode, request.task + ".repair")
            tin, tout = tin + rin, tout + rout
            parsed = parse_json(text, request.schema)  # a second failure falls through to the next provider
        return LLMResponse(text, parsed, provider.name, model, tin, tout)

    def _with_retries(
        self, provider: Provider, model: str, messages: list[dict[str, Any]], json_mode: bool, task: str
    ) -> tuple[str, int, int]:
        for attempt in range(1, self.max_attempts + 1):
            try:
                text, tin, tout = provider.chat(model, messages, json_mode)
            except ProviderError as exc:
                self.ledger.record_error(task, provider.name, model, attempt, exc.message)
                if not exc.retryable or attempt == self.max_attempts:
                    raise
                delay = exc.retry_after if exc.retry_after is not None else self.base_backoff * 2 ** (attempt - 1)
                time.sleep(delay)
                continue
            self.ledger.record(task, provider.name, model, tin, tout)
            return text, tin, tout
        raise AssertionError("unreachable")


def build_client(mode: RunMode, cache: DiskCache, ledger: TokenLedger) -> LLMClient:
    providers: list[Provider] = []
    if mode is RunMode.LIVE:
        rpm = int(os.environ.get("LLM_RPM_LIMIT", "10") or "10")
        no_thinking = os.environ.get("LLM_DISABLE_THINKING", "0").strip() == "1"
        for name in (n.strip().lower() for n in os.environ.get("LLM_PROVIDERS", "").split(",")):
            if not name:
                continue
            base_url = os.environ.get(f"{name.upper()}_BASE_URL", "").strip()
            api_key = os.environ.get(f"{name.upper()}_API_KEY", "").strip()
            if base_url and api_key:
                providers.append(OpenAICompatProvider(name, base_url, api_key, rpm, no_thinking))
    return LLMClient(mode=mode, cache=cache, ledger=ledger, providers=providers)
