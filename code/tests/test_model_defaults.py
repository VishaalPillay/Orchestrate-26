"""A checkout without .env must still name the models that produced the cache."""

from __future__ import annotations

import pytest

from pcda import config
from pcda.llm.client import LLMRequest, LLMUnavailable


@pytest.mark.parametrize("name", sorted(config.MODEL_DEFAULTS))
def test_default_model_when_env_is_unset(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.delenv(name, raising=False)
    assert LLMRequest("t", "p", name).model == config.MODEL_DEFAULTS[name]


def test_environment_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERCEPTION_MODEL", "other-model")
    assert LLMRequest("t", "p", "PERCEPTION_MODEL").model == "other-model"


def test_unknown_model_variable_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_SUCH_MODEL", raising=False)
    with pytest.raises(LLMUnavailable):
        _ = LLMRequest("t", "p", "NO_SUCH_MODEL").model
