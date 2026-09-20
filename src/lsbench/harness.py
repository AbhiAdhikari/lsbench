"""Run tasks against a server under test with the model held fixed.

The harness is the part that must be boringly correct. Every knob that could
differ between servers is pinned in RunConfig so a score difference is
attributable to the server and nothing else.

Output per run, under results/<server>/<timestamp>/:
  report.json         config, server pin, per-dimension aggregates, instability
  results.jsonl       one TaskResult per task per repeat
  trajectories.jsonl  the full transcript of every run — what was called, what
                      came back, what the model said
  provenance.jsonl    provmcp ledger of every tool call, with source releases
  server-logs/        each stdio server's stderr, kept out of the terminal
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .agent import Toolset, Trajectory, run_trajectory
from .config import RunConfig
from .model import ModelClient
from .rules import default_rules
from .scoring import TaskResult, aggregate, instability, measured, score_task, unstable_ids
from .servers import ServerRegistry, ServerSpec
from .tasks import Task

log = logging.getLogger(__name__)

RESULTS_DIR = Path("results")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


async def run_server(
    model: ModelClient,
    registry: ServerRegistry,
    server_id: str,
    tasks: list[Task],
    cfg: RunConfig,
    *,
    distractors: bool = True,
    out_dir: Path = RESULTS_DIR,
    signing_key=None,
) -> dict:
    server = registry.get(server_id)
    tasks = [t for t in tasks if t.domain == server.domain]
    if not tasks:
        raise ValueError(f"no verified tasks for domain {server.domain!r} (server {server_id})")
    dist = registry.distractors_for(server) if distractors else []

    run_dir = Path(out_dir) / server_id / _now()
    run_dir.mkdir(parents=True, exist_ok=True)
    runs: list[list[TaskResult]] = []
    trajectories: list[Trajectory] = []

    budget_exhausted = False
    async with Toolset(server, dist, run_dir / "provenance.jsonl", signing_key,
                       server_log_dir=run_dir / "server-logs") as toolset:
        log.info("%s: %d tools offered (%d distractor servers)", server_id, len(toolset.tools), len(dist))
        for rep in range(cfg.repeats):
            if budget_exhausted:
                break
            log.info("repeat %d/%d", rep + 1, cfg.repeats)
            results: list[TaskResult] = []
            for task in tasks:
                traj = await run_trajectory(model, toolset, task.id, task.prompt, cfg)
                res = score_task(task, traj, server)
                log.info("  %-18s %-12s %.2f  %s", task.id, task.dimension.value, res.score, res.notes)
                results.append(res)
                trajectories.append(traj)
                spent = measured([r for run in runs for r in run] + results, cfg.model).get("estimated_usd")
                if cfg.max_usd is not None and spent is not None and spent >= cfg.max_usd:
                    log.error("estimated spend $%.2f reached --max-usd %.2f; stopping after %s",
                              spent, cfg.max_usd, task.id)
                    budget_exhausted = True
                    break
            runs.append(results)

    flat = [r for run in runs for r in run]
    unstable = instability(runs)
    report = {
        "lsbench_version": __version__,
        "generated_at": datetime.now(UTC).isoformat(),
        "config": cfg.as_dict(),
        "server": server.model_dump(exclude={"inproc"}),
        "distractors": [d.model_dump(exclude={"inproc"}) for d in dist],
        "fairness_problems": server.pinned(),
        "scoring_rules": {"source": default_rules().source, "sha256": default_rules().sha256},
        "tasks": [t.id for t in tasks],
        "fuzzy_fraction": (sum(t.fuzzy_fraction for t in tasks) / len(tasks)) if tasks else 0.0,
        "dimensions": aggregate(flat, unstable_ids(unstable)),
        "measured": measured(flat, cfg.model),
        "unstable": unstable,
        "budget_exhausted": budget_exhausted,   # partial run: never publishable as-is
        "provenance": str(run_dir / "provenance.jsonl"),
    }
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    with (run_dir / "results.jsonl").open("w") as fh:
        for r in flat:
            fh.write(json.dumps(asdict(r), default=str) + "\n")
    with (run_dir / "trajectories.jsonl").open("w") as fh:
        for t in trajectories:
            fh.write(json.dumps(t.to_dict(), default=str) + "\n")
    report["run_dir"] = str(run_dir)
    return report


def load_trajectories(run_dir: Path) -> list[Trajectory]:
    from .agent import ToolCall
    from .model import Usage

    out = []
    error_like = default_rules().error_like
    for line in (Path(run_dir) / "trajectories.jsonl").read_text().splitlines():
        d = json.loads(line)
        for c in d["calls"]:   # recorded under the rules of the day; rescore under today's
            c["error_like"] = bool(error_like.search(c["result_text"][:200]))
        if d.get("stop_reason") == "max_tokens" and not d.get("failure"):
            d["failure"] = "max_tokens: turn truncated by the harness output cap"   # pre-fix recordings
        d["calls"] = [ToolCall(**c) for c in d["calls"]]
        d["usage"] = Usage(**d["usage"])
        out.append(Trajectory(**d))
    return out


def rescore(run_dir: Path, registry: ServerRegistry, tasks: list[Task]) -> dict:
    """Re-score a saved run under the current scoring rules and task claims,
    without calling any server or model. Writes report.rescored.json beside the
    original so the two can be diffed; the original is never touched."""
    run_dir = Path(run_dir)
    original = json.loads((run_dir / "report.json").read_text())
    server = registry.get(original["server"]["id"])
    by_id = {t.id: t for t in tasks}
    trajectories = load_trajectories(run_dir)
    repeats = original["config"]["repeats"]
    per_rep = max(1, len(trajectories) // repeats)
    runs: list[list[TaskResult]] = []
    for i in range(0, len(trajectories), per_rep):
        chunk = trajectories[i:i + per_rep]
        runs.append([score_task(by_id[t.task_id], t, server) for t in chunk if t.task_id in by_id])
    flat = [r for run in runs for r in run]
    unstable = instability(runs)
    report = {
        **original,
        "rescored_from": str(run_dir / "report.json"),
        "rescored_at": datetime.now(UTC).isoformat(),
        "scoring_rules": {"source": default_rules().source, "sha256": default_rules().sha256},
        "dimensions": aggregate(flat, unstable_ids(unstable)),
        "measured": measured(flat, original["config"]["model"]),
        "unstable": unstable,
        "run_dir": str(run_dir),
    }
    (run_dir / "report.rescored.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    with (run_dir / "results.rescored.jsonl").open("w") as fh:
        for r in flat:
            fh.write(json.dumps(asdict(r), default=str) + "\n")
    return report


async def compare_servers(
    model: ModelClient, registry: ServerRegistry, server_ids: list[str],
    tasks: list[Task], cfg: RunConfig, **kw,
) -> list[dict]:
    """Sequential; each server's files are written before the next starts, so a
    crash or an exhausted budget loses only the server in progress. The
    --max-usd cap is for the whole compare: what earlier servers spent is
    subtracted before the next one runs."""
    reports: list[dict] = []
    remaining = cfg.max_usd
    for sid in server_ids:
        server_cfg = cfg if remaining is None else RunConfig(**{**cfg.as_dict(), "max_usd": remaining})
        if remaining is not None and remaining <= 0:
            log.error("budget exhausted before %s; skipping", sid)
            break
        report = await run_server(model, registry, sid, tasks, server_cfg, **kw)
        reports.append(report)
        spent = (report.get("measured") or {}).get("estimated_usd") or 0.0
        if remaining is not None:
            remaining -= spent
        if report.get("budget_exhausted"):
            break
    return reports


def server_ready(server: ServerSpec) -> list[str]:
    return server.pinned()
