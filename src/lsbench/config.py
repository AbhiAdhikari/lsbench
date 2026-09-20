"""Everything held fixed across every run.

A score difference between two servers is attributable to the server only if
nothing here differed. Every field is written into every report, so a reader can
see what was pinned. Change one and you have a new report series, not a new data
point in the old one.

On determinism: current Claude models (Sonnet 5, Opus 5) reject `temperature`
and have no seed parameter. There is no knob that makes a run reproducible. The
honest substitute is repetition — every task runs `repeats` times and any task
that passes in some runs and fails in others is reported as unstable, which
counts as a failure. See scoring.instability.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# The proxy prefixes every tool with its server id. That is what a real client
# (Claude Desktop, Claude Code) does too, so the model sees realistic names.
TOOL_SEP = "__"

SYSTEM_PROMPT = """\
You are a research assistant with access to life-sciences data tools.
Answer the user's question using the tools. When you have the answer, state it
plainly in one short paragraph. Include specific identifiers (accessions, gene
symbols, IDs) and numbers you found. If a tool reports that something does not
exist or returns an error, say so — do not guess or invent records. If a tool
result was cut short or paginated, say that too.\
"""


@dataclass(frozen=True)
class RunConfig:
    model: str = "claude-opus-5"
    effort: str = "high"                 # output_config.effort; adaptive thinking on
    max_tokens: int = 16000              # per assistant turn, thinking included; 4096 truncated one answer
    max_turns: int = 12                  # tool-call rounds before the trajectory is cut off
    tool_result_max_chars: int = 40_000  # what the model sees; the raw size is still measured
    repeats: int = 3
    max_usd: float | None = None         # stop the run when the estimated spend passes this
    system_prompt: str = SYSTEM_PROMPT
    tool_sep: str = TOOL_SEP
    extras: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)
