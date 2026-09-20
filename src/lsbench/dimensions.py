"""Scoring dimensions.

Each dimension is a property of SERVER DESIGN, not model capability. That is the
whole thesis: the model is held fixed, so a score difference between two servers
is attributable to how the servers were built.

Prior benchmarks (MCP-Bench, MCP-Universe, MCP-Atlas) vary the model and hold
servers fixed. They answer "which model is better at tool use." lsbench answers
"which server is better to give a model."

ROUTING and RECOVERY are the dimensions that matter most. They score tool
descriptions and error messages — the parts a wrapper generator cannot produce
and the parts nobody tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Dimension(StrEnum):
    ROUTING = "routing"
    BUDGET = "budget"
    RECOVERY = "recovery"
    CORRECTNESS = "correctness"
    LATENCY = "latency"
    COST = "cost"


@dataclass(frozen=True)
class DimensionSpec:
    name: Dimension
    question: str
    what_it_measures: str
    server_side_cause: str
    higher_is_better: bool = True


SPECS: dict[Dimension, DimensionSpec] = {
    Dimension.ROUTING: DimensionSpec(
        name=Dimension.ROUTING,
        question="Does the model call the right tool first, without being told which?",
        what_it_measures=(
            "Fraction of tasks where the first tool call is the correct one, with "
            "distractor servers loaded. Scored on the call, not the final answer — "
            "a model can recover from a bad first call and still answer correctly, "
            "which hides the defect."
        ),
        server_side_cause=(
            "Tool descriptions. A good description says when to use this tool AND "
            "when to use a different one. Servers that mirror an API's structure "
            "(one tool per endpoint) rather than the user's task fail here."
        ),
    ),
    Dimension.BUDGET: DimensionSpec(
        name=Dimension.BUDGET,
        question="Does the response stay bounded, and does it admit when it truncates?",
        what_it_measures=(
            "Response token count against a declared cap, plus whether truncation "
            "was disclosed. SILENT TRUNCATION IS SCORED AS A FAILURE EVEN IF THE "
            "ANSWER IS CORRECT — a model that doesn't know it saw a partial result "
            "will reason wrongly on the next turn."
        ),
        server_side_cause=(
            "Whether the server summarizes and paginates or dumps raw payloads. "
            "The clearest separator between wrapper and designed server."
        ),
        higher_is_better=True,
    ),
    Dimension.RECOVERY: DimensionSpec(
        name=Dimension.RECOVERY,
        question="After an error, does the model recover without human help?",
        what_it_measures=(
            "Fraction of deliberately-failing tasks (bad accession, empty search, "
            "stale cursor, rate limit) where the model reaches a correct answer "
            "using only the error text. Hallucinating past the error scores zero "
            "and is tracked separately — it is worse than giving up."
        ),
        server_side_cause=(
            "Error messages. An error is an instruction to the model, not a status "
            "report. '404 Not Found' scores near zero; 'No series matches X; "
            "accessions are GSE + 1-6 digits; for a sample ID call get_sample_table' "
            "scores well."
        ),
    ),
    Dimension.CORRECTNESS: DimensionSpec(
        name=Dimension.CORRECTNESS,
        question="Is the returned biology actually right?",
        what_it_measures=(
            "Claims-based partial credit against hand-verified ground truth. Each "
            "task declares independent verifiable claims; score is the fraction "
            "present and correct. No holistic LLM-judge scoring."
        ),
        server_side_cause=(
            "Metadata resolution. Free-text characteristics, inconsistent organism "
            "strings, assay type buried in a title, SuperSeries vs SubSeries sample "
            "counts. Servers that pass raw metadata through push this work onto the "
            "model, which does it unreliably."
        ),
    ),
    Dimension.LATENCY: DimensionSpec(
        name=Dimension.LATENCY,
        question="Wall-clock time to a complete answer.",
        what_it_measures="Median and p95 seconds per task, cache cleared.",
        server_side_cause="Sequential vs parallel upstream calls; caching; payload size.",
        higher_is_better=False,
    ),
    Dimension.COST: DimensionSpec(
        name=Dimension.COST,
        question="Total tokens consumed to reach an answer.",
        what_it_measures=(
            "Input + output tokens across the whole trajectory. Correlates with "
            "BUDGET but is distinct: a server can stay under a per-response cap and "
            "still burn tokens by forcing many round trips."
        ),
        server_side_cause="Response verbosity and how many calls the tool surface forces.",
        higher_is_better=False,
    ),
}

# Dimensions where a difference between servers is most diagnostic. Lead the
# report with these.
HEADLINE = (Dimension.ROUTING, Dimension.RECOVERY, Dimension.BUDGET)


def report_order() -> list[Dimension]:
    rest = [d for d in Dimension if d not in HEADLINE]
    return [*HEADLINE, *rest]
