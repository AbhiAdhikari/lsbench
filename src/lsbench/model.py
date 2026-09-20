"""The fixed model behind the benchmark.

One interface, two implementations: the real Claude API, and a scripted model
used by the tests so the harness can be exercised without credentials. The
harness only sees `ModelResponse`, so a scripted trajectory and a live one are
scored by identical code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import RunConfig


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def prompt_tokens(self) -> int:
        """Everything the model read this call, cached or not."""
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.output_tokens


@dataclass
class ModelResponse:
    content: list[dict[str, Any]]      # API wire shape: text / tool_use / thinking blocks
    stop_reason: str
    usage: Usage = field(default_factory=Usage)

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    @property
    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.content if b.get("type") == "text")


class ModelClient(Protocol):
    async def create(
        self, *, system: str, messages: list[dict], tools: list[dict], cfg: RunConfig
    ) -> ModelResponse: ...


class AnthropicModel:
    """Claude via the official SDK. Adaptive thinking on, effort pinned, no
    sampling parameters (current models reject them)."""

    def __init__(self, client: Any | None = None):
        if client is None:
            import anthropic

            client = anthropic.AsyncAnthropic()
        self._client = client

    async def create(
        self, *, system: str, messages: list[dict], tools: list[dict], cfg: RunConfig
    ) -> ModelResponse:
        # Prompt caching: the system prompt and tool list are stable for the whole
        # run; the top-level cache_control also caches the conversation so far, so
        # each round re-reads the growing transcript at cache price instead of full
        # price. Without this an 8-round trajectory re-sends every prior tool
        # result uncached every turn.
        resp = await self._client.messages.create(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive"},
            output_config={"effort": cfg.effort},
            tools=tools,
            messages=messages,
            cache_control={"type": "ephemeral"},
        )
        u = resp.usage
        return ModelResponse(
            content=[b.model_dump(exclude_none=True) for b in resp.content],
            stop_reason=resp.stop_reason or "end_turn",
            usage=Usage(
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                cache_creation_input_tokens=u.cache_creation_input_tokens or 0,
                cache_read_input_tokens=u.cache_read_input_tokens or 0,
            ),
        )
