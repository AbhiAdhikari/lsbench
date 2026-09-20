"""The trajectory recorder: token deltas, truncation, error detection, cutoff."""

from conftest import ScriptedModel, text, tool

from lsbench.agent import Toolset, run_trajectory
from lsbench.config import RunConfig


async def test_tokens_measured_from_usage_deltas_and_harness_cap(registry, tmp_path):
    server = registry.get("wrapper")
    model = ScriptedModel({"p": [tool(("wrapper__esummary", {"db": "gds", "id": "GSE115978"})), text("done")]})
    cfg = RunConfig(model="scripted", tool_result_max_chars=1000)
    async with Toolset(server, [], tmp_path / "l.jsonl") as ts:
        traj = await run_trajectory(model, ts, "t", "p", cfg)
    call = traj.calls[0]
    assert call.result_chars > 100_000                 # the raw server payload
    assert call.harness_truncated is True
    assert call.tokens is not None and 0 < call.tokens < call.result_chars  # measured on the *shown* text
    assert traj.api_calls == 2 and traj.final_text == "done"
    assert "lsbench: tool result truncated" not in call.result_text  # raw text kept raw


async def test_error_detection_flagged_and_unflagged(registry, tmp_path):
    server = registry.get("designed")
    model = ScriptedModel({"p": [tool(("designed__get_series", {"accession": "GSE0"})), text("not found")]})
    async with Toolset(server, [], tmp_path / "l.jsonl") as ts:
        traj = await run_trajectory(model, ts, "t", "p", RunConfig(model="scripted"))
    assert traj.calls[0].is_error is True                # MCPServer flags raised exceptions
    assert traj.calls[0].surfaced_error is True


async def test_cutoff_at_max_turns(registry, tmp_path):
    server = registry.get("designed")
    looping = ScriptedModel({"p": [tool(("designed__get_series", {"accession": "GSE176078"}))]})
    cfg = RunConfig(model="scripted", max_turns=3)
    async with Toolset(server, [], tmp_path / "l.jsonl") as ts:
        traj = await run_trajectory(looping, ts, "t", "p", cfg)
    assert traj.cut_off is True and traj.rounds == 3 and len(traj.calls) == 3


async def test_unknown_tool_is_a_recorded_error_not_a_crash(registry, tmp_path):
    server = registry.get("designed")
    model = ScriptedModel({"p": [tool(("nope__tool", {})), text("gave up")]})
    async with Toolset(server, [], tmp_path / "l.jsonl") as ts:
        traj = await run_trajectory(model, ts, "t", "p", RunConfig(model="scripted"))
    assert traj.calls[0].is_error and traj.failure == "" and traj.final_text == "gave up"
