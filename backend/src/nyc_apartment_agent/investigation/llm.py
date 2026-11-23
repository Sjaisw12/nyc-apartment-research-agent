"""Optional LLM access with hard limits. Off unless LLM_ENABLED=1 and a key is configured.

- Provider is configurable: Anthropic (official SDK) or OpenAI (HTTP chat completions).
- Every call goes through an LLMBudget: at most `llm_max_calls` per investigation (2 by
  default) and a process-wide cap as spend protection.
- Keys are read from settings as SecretStr and are never logged or returned by the API.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "openai": "gpt-5-mini"}


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(self, system: str, user: str, max_tokens: int) -> str: ...


_process_lock = threading.Lock()
_process_calls = 0


@dataclass
class LLMBudget:
    max_calls: int
    process_cap: int
    calls: int = 0
    log: list[str] = field(default_factory=list)

    def take(self) -> None:
        global _process_calls
        if self.calls >= self.max_calls:
            raise LLMError("per-investigation LLM call limit reached")
        with _process_lock:
            if _process_calls >= self.process_cap:
                raise LLMError("process-wide LLM call cap reached (spend protection)")
            _process_calls += 1
        self.calls += 1


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, settings: Settings) -> None:
        import anthropic

        self.model = settings.llm_model or DEFAULT_MODELS["anthropic"]
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
            max_retries=1,
        )

    def complete(self, system: str, user: str, max_tokens: int) -> str:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low"},
        )
        if resp.stop_reason == "refusal":
            raise LLMError("the model declined the request")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if not text:
            raise LLMError(f"empty response (stop_reason={resp.stop_reason})")
        return text


class OpenAIClient:
    provider = "openai"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.llm_model or DEFAULT_MODELS["openai"]
        self._key = settings.openai_api_key.get_secret_value()
        self._timeout = settings.llm_timeout_seconds

    def complete(self, system: str, user: str, max_tokens: int) -> str:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self._key}"},
            json={
                "model": self.model,
                "max_completion_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=self._timeout,
        )
        if resp.status_code != 200:
            raise LLMError(f"OpenAI HTTP {resp.status_code}")
        return resp.json()["choices"][0]["message"]["content"] or ""


def llm_status(settings: Settings | None = None) -> tuple[bool, str]:
    s = settings or get_settings()
    if not s.llm_enabled:
        return False, "LLM analysis is disabled (LLM_ENABLED is not set)."
    if s.llm_max_calls == 0:
        return False, "LLM analysis is disabled (LLM_MAX_CALLS=0)."
    key = s.anthropic_api_key if s.llm_provider == "anthropic" else s.openai_api_key
    if key is None:
        return False, f"LLM analysis is enabled but no {s.llm_provider} API key is configured."
    return True, f"{s.llm_provider} configured"


def make_client(settings: Settings | None = None) -> LLMClient | None:
    s = settings or get_settings()
    ok, _ = llm_status(s)
    if not ok:
        return None
    try:
        return AnthropicClient(s) if s.llm_provider == "anthropic" else OpenAIClient(s)
    except Exception as exc:  # never let client construction break an investigation
        logger.warning("LLM client unavailable: %s", type(exc).__name__)
        return None
