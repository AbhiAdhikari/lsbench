"""End to end, offline: real task files, real proxy, real scoring, scripted decisions."""

import json
from pathlib import Path

from conftest import ScriptedModel, text, tool
from provmcp.ledger import Ledger

from lsbench.config import RunConfig
from lsbench.harness import run_server
from lsbench.tasks import load_tasks

P = {t.id: t.prompt for t in load_tasks()}

# A model that behaves well against the designed server.
GOOD = {
    P["geo-correct-001"]: [tool(("designed__get_series", {"accession": "GSE176078"})),
                           text("GSE176078 is Homo sapiens, expression profiling by high throughput sequencing, 26 samples.")],
    P["geo-correct-002"]: [tool(("designed__get_series", {"accession": "GSE120575"})),
                           text("Platform GPL18573; PubMed 30388456.")],
    P["geo-correct-003"]: [tool(("designed__get_series", {"accession": "GSE239940"})),
                           text("Yes, GSE239940 is a SuperSeries of GSE239938 (3 samples) and GSE239939 (21 samples).")],
    P["geo-correct-004"]: [tool(("designed__get_sample", {"accession": "GSM5354513"})),
                           text("Clinical subtype HER2+/ER+, tissue: primary breast tumor.")],
    P["geo-route-001"]: [tool(("designed__get_series", {"accession": "GSE176078"})), text("...")],
    P["geo-route-002"]: [tool(("designed__search_series", {"query": "pancreatic organoid scRNA-seq"})), text("...")],
    P["geo-route-003"]: [tool(("designed__get_sample", {"accession": "GSM5354513"})), text("...")],
    P["geo-route-004"]: [tool(("clinicaltrials__search_trials", {"condition": "pancreatic cancer"})), text("...")],
    P["geo-route-005"]: [tool(("pubmed__search_abstracts", {"query": "CRISPR base editing T cells"})), text("...")],
    P["geo-budget-001"]: [tool(("designed__get_series", {"accession": "GSE115978"})), text("Melanoma scRNA-seq, human, ~7,186 samples.")],
    P["geo-budget-002"]: [tool(("designed__list_samples", {"accession": "GSE115978"})),
                          text("There are 7186 samples; the tool showed the first 50 of 7186.")],
    P["geo-recover-001"]: [tool(("designed__get_series", {"accession": "GSE999999999"})),
                           text("GSE999999999 was not found in GEO; the accession may be wrong.")],
    P["geo-recover-002"]: [tool(("designed__search_series", {"query": "pancreatic organoid scRNA-seq", "year": 2031})),
                           tool(("designed__search_series", {"query": "pancreatic organoid scRNA-seq"})),
                           text("No series are dated 2031; dropping the year, GSE176078 and GSE120575 match.")],
}


async def test_designed_server_scores_well(registry, cfg, tmp_path):
    model = ScriptedModel(GOOD)
    report = await run_server(model, registry, "designed", load_tasks(), cfg, out_dir=tmp_path)
    d = report["dimensions"]
    assert d["correctness"]["mean"] == 1.0
    assert d["routing"]["mean"] == 1.0 and d["routing"]["n"] == 5
    assert d["budget"]["mean"] == 1.0
    assert d["recovery"]["mean"] == 1.0 and d["recovery"]["unexercised"] == 0
    assert all(row["hallucination_rate"] == 0 for row in d.values())
    assert report["unstable"] == [] and report["fairness_problems"] == []
    assert len(report["scoring_rules"]["sha256"]) == 64
    assert report["measured"]["n"] == 13

    # every tool call the benchmark made is in a verifiable provenance ledger
    run_dir = Path(report["run_dir"])
    assert Ledger(run_dir / "provenance.jsonl").verify() == []
    entries = list(Ledger(run_dir / "provenance.jsonl").entries())
    assert len(entries) == 14 and entries[0].source_release == "GEO test"
    assert (run_dir / "results.jsonl").exists() and (run_dir / "trajectories.jsonl").exists()
    traj = [json.loads(line) for line in (run_dir / "trajectories.jsonl").read_text().splitlines()]
    assert all(t["calls"][0]["tokens"] is not None for t in traj if t["calls"])  # measured, not None


async def test_wrapper_server_is_penalised_where_it_should_be(registry, cfg, tmp_path):
    # Same "model" intent, but the wrapper only offers esearch/esummary: raw dumps,
    # empty results with no error, no pagination.
    scripts = {
        P["geo-route-001"]: [tool(("wrapper__esearch", {"db": "gds", "term": "GSE176078"})), text("...")],
        P["geo-budget-002"]: [tool(("wrapper__esummary", {"db": "gds", "id": "GSE115978"})), text("Listed.")],
        P["geo-recover-001"]: [tool(("wrapper__esummary", {"db": "gds", "id": "GSE999999999"})),
                               text("GSE999999999 is a study of expression profiling of tumors; 12 samples were collected.")],
        P["geo-correct-001"]: [tool(("wrapper__esummary", {"db": "gds", "id": "GSE176078"})),
                               text("GSE176078: Homo sapiens, 26 samples, sequencing.")],
    }
    model = ScriptedModel(scripts)
    tasks = [t for t in load_tasks() if t.prompt in scripts]
    report = await run_server(model, registry, "wrapper", tasks, cfg, out_dir=tmp_path)
    rows = {json.loads(line)["task_id"]: json.loads(line)
            for line in (Path(report["run_dir"]) / "results.jsonl").read_text().splitlines()}

    assert rows["geo-route-001"]["claims_passed"] == 0                 # search-first is a routing failure
    assert rows["geo-budget-002"]["claim_results"] == {"under_cap": False, "truncation_disclosed": False}
    assert rows["geo-recover-001"]["exercised"] is False               # silent success for a bad accession
    assert "silent" in report["dimensions"]["recovery"] or report["dimensions"]["recovery"]["unexercised"] == 1
    # the answer had the right strings but nothing in the tool output supported
    # them: no credit, and flagged
    assert rows["geo-correct-001"]["claims_passed"] == 0
    assert rows["geo-correct-001"]["hallucinated"] is True


async def test_instability_is_reported_as_failure(registry, tmp_path):
    from lsbench.config import RunConfig

    flaky = {P["geo-route-001"]: [tool(("designed__get_series", {"accession": "GSE176078"})), text("x")]}
    model = ScriptedModel(flaky)
    calls = {"n": 0}
    orig = model.create

    async def alternating(**kw):
        if len(kw["messages"]) == 1:      # a new trajectory
            calls["n"] += 1
        if calls["n"] % 2 == 0:            # every other repeat never calls a tool
            kw["messages"] = [{"role": "user", "content": "unknown"}]
        return await orig(**kw)

    model.create = alternating
    tasks = [t for t in load_tasks() if t.id == "geo-route-001"]
    report = await run_server(model, registry, "designed", tasks, RunConfig(repeats=2, model="scripted"),
                              out_dir=tmp_path)
    assert report["unstable"] and "geo-route-001" in report["unstable"][0]


async def test_unpinned_server_is_flagged_not_publishable(registry, cfg, tmp_path):
    registry.servers[0].version_tested = "TODO"
    model = ScriptedModel(GOOD)
    tasks = [t for t in load_tasks() if t.id == "geo-route-001"]
    report = await run_server(model, registry, "designed", tasks, cfg, out_dir=tmp_path)
    assert "version_tested not pinned" in report["fairness_problems"]


async def test_max_usd_stops_the_run_and_flags_it(registry, tmp_path):
    from lsbench.config import RunConfig

    class Pricey(ScriptedModel):
        async def create(self, **kw):
            r = await super().create(**kw)
            r.usage.output_tokens = 1_000_000          # $25 per call at Opus list price
            return r

    model = Pricey(GOOD)
    cfg = RunConfig(repeats=3, model="claude-opus-5", max_usd=30.0)
    report = await run_server(model, registry, "designed", load_tasks(), cfg, out_dir=tmp_path)
    assert report["budget_exhausted"] is True
    assert report["measured"]["n"] < 13 * 3
    assert report["measured"]["estimated_usd"] >= 30.0


async def test_max_tokens_is_a_harness_failure_not_a_zero(registry, tmp_path):
    from lsbench.model import ModelResponse, Usage

    class Truncating(ScriptedModel):
        async def create(self, **kw):
            r = await super().create(**kw)
            if r.stop_reason == "end_turn":
                return ModelResponse(content=[], stop_reason="max_tokens", usage=Usage())
            return r

    tasks = [t for t in load_tasks() if t.id == "geo-correct-001"]
    report = await run_server(Truncating(GOOD), registry, "designed", tasks, RunConfig(repeats=2, model="scripted"),
                              out_dir=tmp_path)
    row = report["dimensions"]["correctness"]
    assert row["failures"] == 2 and row["unexercised"] == 2 and row["n"] == 0
    assert report["unstable"] == []
