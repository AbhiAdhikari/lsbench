"""Drive the fixed model against a toolset and capture the whole trajectory.

The toolset is a provmcp proxy over the server under test plus the distractors,
opened in-process. Two consequences:

  1. Every benchmark call is recorded in a signed, hash-chained ledger with the
     data-source release at the time. A published score comes with its own
     provenance bundle; a reviewer can replay it and see what has moved since.
  2. Tools reach the model as `<server_id>__<tool>`, which is also what Claude
     Desktop and Claude Code show a model. Realistic names, and server
     attribution for free.

The loop is the manual one: we need the first tool call before any recovery
masks it, per-result token counts, and the raw result size before the harness
caps what the model sees.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mcp import Client
from mcp_types import CallToolResult
from provmcp.config import Config as ProvConfig
from provmcp.ledger import Ledger
from provmcp.proxy import OWN_PREFIX, build_proxy

from .config import RunConfig
from .model import ModelClient, ModelResponse, Usage
from .rules import default_rules
from .servers import ServerSpec

log = logging.getLogger(__name__)

HARNESS_CUT = "\n\n[lsbench: tool result truncated by the harness at {n} characters; the server returned {total}]"


@dataclass
class ToolCall:
    round: int
    server: str
    tool: str
    name: str                       # as the model saw it
    arguments: dict
    result_text: str                # raw, as the server returned it
    result_chars: int
    is_error: bool                  # the server flagged it
    error_like: bool                # it reads like an error even if unflagged
    latency_s: float
    tokens: int | None = None       # measured from API usage deltas; None if never sent
    harness_truncated: bool = False

    @property
    def surfaced_error(self) -> bool:
        return self.is_error or self.error_like


@dataclass
class Trajectory:
    task_id: str
    server_id: str
    calls: list[ToolCall] = field(default_factory=list)
    final_text: str = ""
    stop_reason: str = ""
    rounds: int = 0
    api_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    latency_s: float = 0.0
    cut_off: bool = False           # hit max_turns with tool calls still pending
    failure: str = ""               # harness/transport failure, distinct from a tool error
    tools_offered: list[str] = field(default_factory=list)
    ledger_path: str = ""

    @property
    def first_call(self) -> ToolCall | None:
        return self.calls[0] if self.calls else None

    @property
    def total_tokens(self) -> int:
        return self.usage.total

    def to_dict(self) -> dict:
        return asdict(self)


def result_text(result: CallToolResult) -> str:
    parts = []
    for block in result.content:
        t = getattr(block, "text", None)
        if t is not None:
            parts.append(t)
        else:
            parts.append(json.dumps(block.model_dump(mode="json", exclude_none=True)))
    if not parts and result.structured_content is not None:
        parts.append(json.dumps(result.structured_content))
    return "\n".join(parts)


def mcp_tool_to_anthropic(tool: Any) -> dict:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.input_schema or {"type": "object", "properties": {}},
    }


class Toolset:
    """provmcp proxy over the server under test + distractors, in-process."""

    def __init__(self, under_test: ServerSpec, distractors: list[ServerSpec], ledger_path: Path,
                 signing_key: Any = None, server_log_dir: Path | None = None):
        self.under_test = under_test
        self.distractors = distractors
        self.ledger_path = Path(ledger_path)
        self._cfg = ProvConfig(
            servers=[s.to_downstream() for s in [under_test, *distractors]],
            ledger=self.ledger_path,
            server_log_dir=server_log_dir,
        )
        self._ledger = Ledger(self.ledger_path, signing_key=signing_key)
        self._client: Client | None = None
        self.tools: list[dict] = []

    async def __aenter__(self) -> Toolset:
        proxy = build_proxy(self._cfg, self._ledger)
        self._client = Client(proxy)
        await self._client.__aenter__()
        listed = (await self._client.list_tools()).tools
        # The proxy's own tools are not part of any server under test.
        self.tools = [mcp_tool_to_anthropic(t) for t in listed if not t.name.startswith(OWN_PREFIX)]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        assert self._client is not None
        await self._client.__aexit__(*exc)

    async def call(self, name: str, arguments: dict) -> tuple[str, bool]:
        assert self._client is not None
        result = await self._client.call_tool(name, arguments)
        return result_text(result), bool(result.is_error)


async def run_trajectory(
    model: ModelClient, toolset: Toolset, task_id: str, prompt: str, cfg: RunConfig
) -> Trajectory:
    traj = Trajectory(
        task_id=task_id, server_id=toolset.under_test.id,
        tools_offered=[t["name"] for t in toolset.tools], ledger_path=str(toolset.ledger_path),
    )
    messages: list[dict] = [{"role": "user", "content": prompt}]
    pending: list[ToolCall] = []     # results appended but not yet read by the model
    prev_prompt_tokens = 0
    t_start = time.perf_counter()

    try:
        for rnd in range(cfg.max_turns + 1):
            resp: ModelResponse = await model.create(
                system=cfg.system_prompt, messages=messages, tools=toolset.tools, cfg=cfg
            )
            traj.api_calls += 1
            traj.usage.input_tokens += resp.usage.input_tokens
            traj.usage.output_tokens += resp.usage.output_tokens
            traj.usage.cache_creation_input_tokens += resp.usage.cache_creation_input_tokens
            traj.usage.cache_read_input_tokens += resp.usage.cache_read_input_tokens

            # Tool results sent last round were read by this request. Their cost is
            # the growth in prompt tokens minus the assistant turn that preceded them.
            if pending:
                grown = resp.usage.prompt_tokens - prev_prompt_tokens
                total_chars = sum(max(c.result_chars, 1) for c in pending)
                for c in pending:
                    c.tokens = max(0, int(grown * max(c.result_chars, 1) / total_chars))
                pending = []
            prev_prompt_tokens = resp.usage.prompt_tokens + resp.usage.output_tokens

            messages.append({"role": "assistant", "content": resp.content})
            traj.stop_reason = resp.stop_reason
            if resp.stop_reason == "refusal":
                traj.failure = "model refused"
                break
            if resp.stop_reason == "max_tokens":
                traj.failure = "max_tokens: turn truncated by the harness output cap"
                traj.final_text = resp.text
                break
            tool_uses = resp.tool_uses
            if not tool_uses or resp.stop_reason not in ("tool_use", "pause_turn"):
                traj.final_text = resp.text
                break
            if rnd == cfg.max_turns:
                traj.cut_off = True
                traj.final_text = resp.text
                break

            traj.rounds += 1
            results = []
            for tu in tool_uses:
                name, args = tu["name"], tu.get("input") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                server, _, tool = name.partition(cfg.tool_sep)
                t0 = time.perf_counter()
                try:
                    text, is_error = await toolset.call(name, args)
                except Exception as exc:
                    text, is_error = f"error: {type(exc).__name__}: {exc}", True
                latency = time.perf_counter() - t0

                shown = text
                truncated = False
                if len(text) > cfg.tool_result_max_chars:
                    shown = text[: cfg.tool_result_max_chars] + HARNESS_CUT.format(
                        n=cfg.tool_result_max_chars, total=len(text))
                    truncated = True

                call = ToolCall(
                    round=rnd, server=server, tool=tool, name=name, arguments=args,
                    result_text=text, result_chars=len(text), is_error=is_error,
                    error_like=bool(default_rules().error_like.search(text[:200])), latency_s=latency,
                    harness_truncated=truncated,
                )
                traj.calls.append(call)
                pending.append(call)
                results.append({
                    "type": "tool_result", "tool_use_id": tu["id"],
                    "content": shown or "(empty result)", "is_error": is_error,
                })
            messages.append({"role": "user", "content": results})
    except Exception as exc:
        log.exception("trajectory %s on %s failed", task_id, toolset.under_test.id)
        traj.failure = f"{type(exc).__name__}: {exc}"

    for c in pending:  # never read by the model; estimate so cost isn't silently zero
        c.tokens = c.result_chars // 4
    traj.latency_s = time.perf_counter() - t_start
    return traj
