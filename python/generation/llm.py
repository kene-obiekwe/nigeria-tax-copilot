"""Generation model client (D-26, D-27; generation design, sections 7 and 8).

A client takes the request parameters built by `request_params` and returns an `LLMResponse`.
`AnthropicLLMClient` calls the Claude API; tests pass a fake with the same `create` method.

The key is read from .env (LLM_API_KEY) and passed to the SDK explicitly, together with the API
base URL, so the SDK never falls back to ANTHROPIC_API_KEY, a saved profile or an
ANTHROPIC_BASE_URL set for other tools. The key is never printed or logged, and provider errors
are re-raised without their message.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
API_BASE_URL = "https://api.anthropic.com"
MODEL = "claude-sonnet-5-5"
EFFORT = "medium"
MAX_TOKENS = 16_000
TIMEOUT_S = 120.0
MAX_RETRIES = 3

# USD per million tokens, Claude API standard tier, accessed 2026-11-02 from
# https://platform.claude.com/docs/en/about-claude/pricing (D-26).
PRICES = {
    "claude-sonnet-5-5": {"input": 2.00, "cache_write_5m": 2.50, "cache_read": 0.10, "output": 10.00},
}


class GenerationError(RuntimeError):
    """A model call failed. The message names the error type and status only."""


@dataclass
class LLMResponse:
    content: list[dict]           # content blocks as returned (text; thinking is empty when omitted)
    stop_reason: str | None
    stop_details: dict | None
    usage: dict                   # input, output, cache write, cache read tokens
    request_id: str | None
    model: str
    latency_ms: int
    raw: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "".join(block.get("text", "") for block in self.content if block.get("type") == "text")


class LLMClient(Protocol):
    model: str

    def create(self, params: dict) -> LLMResponse: ...


def request_params(system: str, user: str, schema: dict, *, model: str = MODEL, effort: str = EFFORT,
                   max_tokens: int = MAX_TOKENS) -> dict:
    """Messages API parameters: cached system block, one user turn, adaptive thinking, JSON schema
    output. No sampling parameters (the 2026-10-21 API version rejects them)."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    }


def usage_dict(usage) -> dict:
    get = (lambda key: usage.get(key)) if isinstance(usage, dict) else (lambda key: getattr(usage, key, None))
    return {key: int(get(key) or 0) for key in
            ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}


def cost_usd(usage: dict, model: str = MODEL) -> float:
    price = PRICES[model]
    return (usage["input_tokens"] * price["input"] + usage["cache_creation_input_tokens"] * price["cache_write_5m"]
            + usage["cache_read_input_tokens"] * price["cache_read"]
            + usage["output_tokens"] * price["output"]) / 1_000_000


class AnthropicLLMClient:
    """Claude API client for one model. The SDK retries 408, 409, 429, 5xx and connection errors."""

    def __init__(self, api_key: str | None = None, *, model: str = MODEL, timeout: float = TIMEOUT_S,
                 max_retries: int = MAX_RETRIES, http_client=None):
        import anthropic

        if api_key is None:
            load_dotenv(ROOT / ".env")
            api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            raise GenerationError("LLM_API_KEY is not set in .env")
        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key, base_url=API_BASE_URL, timeout=timeout,
                                           max_retries=max_retries, http_client=http_client)

    def __repr__(self) -> str:
        return f"AnthropicLLMClient(model={self.model!r})"

    def create(self, params: dict) -> LLMResponse:
        import anthropic

        started = time.monotonic()
        try:
            message = self._client.messages.create(**params)
        except anthropic.APIStatusError as exc:
            raise GenerationError(f"{type(exc).__name__} (HTTP {exc.status_code})") from None
        except anthropic.AnthropicError as exc:
            raise GenerationError(type(exc).__name__) from None
        raw = message.to_dict()
        details = raw.get("stop_details")
        return LLMResponse(content=raw.get("content", []), stop_reason=raw.get("stop_reason"),
                           stop_details=details, usage=usage_dict(raw.get("usage", {})),
                           request_id=getattr(message, "_request_id", None), model=raw.get("model", self.model),
                           latency_ms=int((time.monotonic() - started) * 1000), raw=raw)
