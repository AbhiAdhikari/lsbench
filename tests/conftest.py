"""Offline fixtures: a scripted model and in-process MCP servers.

The scripted model plays back a trajectory per task, so the harness, the
proxy, the scoring, and the report are exercised exactly as they would be
against Claude — only the decisions are canned."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server import MCPServer

from lsbench.config import RunConfig
from lsbench.model import ModelResponse, Usage
from lsbench.servers import ServerRegistry, ServerSpec


class ScriptedModel:
    """turns[prompt] = list of steps; a step is ("tool", [(name, args), ...]) or ("text", answer).
    Token usage is synthesized so the delta accounting has something to measure."""

    def __init__(self, scripts: dict[str, list[tuple]], default: list[tuple] | None = None):
        self.scripts = scripts
        self.default = default or [("text", "I could not determine the answer.")]
        self.calls: list[dict] = []
        self._cursor: dict[int, int] = {}

    async def create(self, *, system: str, messages: list[dict], tools: list[dict],
                     cfg: RunConfig) -> ModelResponse:
        prompt = messages[0]["content"]
        steps = self.scripts.get(prompt, self.default)
        n_assistant = sum(1 for m in messages if m["role"] == "assistant")
        step = steps[min(n_assistant, len(steps) - 1)]
        self.calls.append({"prompt": prompt, "n_messages": len(messages), "n_tools": len(tools)})

        # prompt tokens grow with everything appended; tool results at 1 token / 4 chars
        prompt_chars = sum(len(str(m["content"])) for m in messages)
        usage = Usage(input_tokens=prompt_chars // 4, output_tokens=20)

        if step[0] == "tool":
            content = [{"type": "tool_use", "id": f"tu{n_assistant}_{i}", "name": name, "input": args}
                       for i, (name, args) in enumerate(step[1])]
            return ModelResponse(content=content, stop_reason="tool_use", usage=usage)
        return ModelResponse(content=[{"type": "text", "text": step[1]}], stop_reason="end_turn", usage=usage)


@pytest.fixture
def designed_server():
    """A GEO-like server with task-shaped tools, bounded output, and errors that instruct."""
    srv = MCPServer("designed")
    series = {
        "GSE176078": {"organism": "Homo sapiens", "n_samples": 26, "type": "Expression profiling by high throughput sequencing", "platform": "GPL18573", "pubmed": ["34493872"]},
        "GSE120575": {"organism": "Homo sapiens", "n_samples": 48, "type": "Expression profiling by high throughput sequencing", "platform": "GPL18573", "pubmed": ["30388456"]},
        "GSE239940": {"organism": "Homo sapiens", "n_samples": 24, "superseries_of": {"GSE239938": 3, "GSE239939": 21}},
        "GSE115978": {"organism": "Homo sapiens", "n_samples": 7186, "type": "Expression profiling by high throughput sequencing"},
    }

    @srv.tool()
    def get_series(accession: str) -> dict:
        """Look up ONE GEO series (GSE accession) and return a bounded summary. Use when the
        user names a GSE accession. For free-text discovery use search_series; for a sample
        (GSM) use get_sample."""
        if accession not in series:
            raise ValueError(f"No series {accession}. GSE accessions are 'GSE' followed by 1-6 digits; "
                             "check the accession or use search_series to find one by description.")
        return {"accession": accession, **series[accession]}

    @srv.tool()
    def search_series(query: str, year: int | None = None, limit: int = 10) -> dict:
        """Search GEO series by free text. Use when the user describes data but gives no accession.
        Returns at most `limit` hits and says how many matched in total."""
        if year and year > 2026:
            raise ValueError(f"No series match query={query!r} with year={year}: GEO holds no records "
                             "dated after 2026. Retry without the year filter.")
        return {"total": 42, "showing": 2, "hits": ["GSE176078", "GSE120575"], "note": "showing 2 of 42; raise limit or refine the query"}

    @srv.tool()
    def get_sample(accession: str) -> dict:
        """Look up ONE GEO sample (GSM accession) and return its characteristics."""
        if accession == "GSM5354513":
            return {"accession": accession, "series": "GSE176078", "characteristics": {"clinical_subtype": "HER2+/ER+", "tissue": "Primary Breast Tumor"}}
        raise ValueError(f"No sample {accession}")

    @srv.tool()
    def list_samples(accession: str, limit: int = 50) -> dict:
        """List sample accessions for a series, paginated. Returns at most `limit` and states the total."""
        n = series[accession]["n_samples"]
        return {"total": n, "showing": min(limit, n), "samples": [f"GSM{i}" for i in range(min(limit, n))],
                "truncated": n > limit, "note": f"showing first {min(limit, n)} of {n}; pass a cursor for more"}

    return srv


@pytest.fixture
def wrapper_server():
    """An endpoint-mirroring server: raw payloads, bare errors, no pagination."""
    srv = MCPServer("wrapper")

    @srv.tool()
    def esearch(db: str, term: str) -> dict:
        """Run NCBI esearch."""
        return {"esearchresult": {"idlist": [], "count": "0"}}

    @srv.tool()
    def esummary(db: str, id: str) -> dict:
        """Run NCBI esummary."""
        if id == "GSE115978":
            return {"result": {"samples": [{"accession": f"GSM{i}", "title": f"cell {i}"} for i in range(7186)]}}
        return {"result": {}}

    return srv


@pytest.fixture
def trials_server():
    srv = MCPServer("trials")

    @srv.tool()
    def search_trials(condition: str) -> dict:
        """Search ClinicalTrials.gov by condition."""
        return {"trials": ["NCT00000001"]}

    return srv


@pytest.fixture
def pubmed_server():
    srv = MCPServer("pubmed")

    @srv.tool()
    def search_abstracts(query: str) -> dict:
        """Search PubMed abstracts."""
        return {"pmids": ["1"]}

    return srv


@pytest.fixture
def registry(designed_server, wrapper_server, trials_server, pubmed_server):
    return ServerRegistry(
        servers=[
            ServerSpec(id="designed", domain="gene-expression", inproc=designed_server,
                       version_tested="test", date_tested="2026-09-20",
                       source="static", source_options={"release": "GEO test"},
                       roles={"series_lookup": ["get_series"], "series_search": ["search_series"],
                              "sample_lookup": ["get_sample"]}),
            ServerSpec(id="wrapper", domain="gene-expression", inproc=wrapper_server,
                       version_tested="test", date_tested="2026-09-20",
                       roles={"series_lookup": ["esummary"], "series_search": ["esearch"]}),
        ],
        distractors=[
            ServerSpec(id="clinicaltrials", domain="clinical-trials", inproc=trials_server),
            ServerSpec(id="pubmed", domain="literature", inproc=pubmed_server),
        ],
    )


@pytest.fixture
def cfg():
    return RunConfig(repeats=1, model="scripted")


def tool(*calls: tuple[str, dict[str, Any]]) -> tuple:
    return ("tool", list(calls))


def text(answer: str) -> tuple:
    return ("text", answer)
