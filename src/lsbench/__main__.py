"""lsbench CLI.

    lsbench validate                       # tasks parse, ground truth flagged, servers pinned
    lsbench tools --server geomcp          # what the model would see, with distractors
    lsbench run --server geomcp --repeats 3
    lsbench run --server geomcp --dimension routing --no-distractors
    lsbench compare geomcp otp-official otp-community
    lsbench show results/geomcp/<ts>/report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .config import RunConfig

log = logging.getLogger("lsbench")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="lsbench")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", help="check tasks and server pins without running anything")

    tasks_p = sub.add_parser("tasks", help="print the benchmark: every task, its claims, and how it was verified")
    tasks_p.add_argument("--domain", default=None)
    tasks_p.add_argument("--dimension", default=None)
    tasks_p.add_argument("--id", default=None, help="one task in full")
    tasks_p.add_argument("--markdown", action="store_true", help="table for a README or write-up")

    tools = sub.add_parser("tools", help="list the tool surface the model would see")
    tools.add_argument("--server", required=True)
    tools.add_argument("--no-distractors", action="store_true")

    def run_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repeats", type=int, default=RunConfig.repeats)
        p.add_argument("--dimension", default=None)
        p.add_argument("--no-distractors", action="store_true")
        p.add_argument("--include-unverified", action="store_true",
                       help="run tasks whose ground truth is not verified (never publishable)")
        p.add_argument("--model", default=RunConfig.model)
        p.add_argument("--effort", default=RunConfig.effort)
        p.add_argument("--out", default="results")
        p.add_argument("--key", default=None, help="provmcp signing key for the provenance ledger")
        p.add_argument("--scoring", default=None, help="override tasks/_scoring.yaml (recorded in the report)")
        p.add_argument("--max-usd", type=float, default=None,
                       help="stop when the estimated spend (list price) passes this; partial runs are flagged")

    run = sub.add_parser("run", help="score one server")
    run.add_argument("--server", required=True, help="id from servers.yaml")
    run_args(run)

    cmp_ = sub.add_parser("compare", help="score several servers side by side")
    cmp_.add_argument("servers", nargs="+")
    run_args(cmp_)

    show = sub.add_parser("show", help="render a saved report")
    show.add_argument("reports", nargs="+")

    rs = sub.add_parser("rescore", help="re-score saved runs under the current rules; no API calls")
    rs.add_argument("run_dirs", nargs="+")
    rs.add_argument("--scoring", default=None, help="alternative tasks/_scoring.yaml")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level="DEBUG" if args.verbose else "INFO", stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("provmcp").setLevel("WARNING")
    return _dispatch(args)


def _dispatch(args: argparse.Namespace) -> int:
    from .report import render_compare, render_run
    from .servers import load_registry
    from .tasks import load_tasks

    if args.cmd == "show":
        reports = [json.loads(Path(p).read_text()) for p in args.reports]
        for r in reports:
            print(render_run(r), "\n")
        if len(reports) > 1:
            print(render_compare(reports))
        return 0

    registry = load_registry()

    if args.cmd == "rescore":
        from .harness import rescore

        if args.scoring:
            from .rules import load_rules, set_default_rules

            set_default_rules(load_rules(args.scoring))
        tasks = load_tasks(include_unverified=True)
        reports = [rescore(Path(d), registry, tasks) for d in args.run_dirs]
        for r in reports:
            print(render_run(r))
            print(f"\n  rescored with rules {r['scoring_rules']['sha256'][:12]} -> {r['run_dir']}/report.rescored.json\n")
        if len(reports) > 1:
            print(render_compare(reports))
        return 0

    if args.cmd == "tasks":
        from .report import render_task, render_tasks_markdown

        tasks = load_tasks(domain=args.domain, dimension=args.dimension, include_unverified=True)
        if args.id:
            tasks = [t for t in tasks if t.id == args.id]
            if not tasks:
                print(f"no task {args.id!r}", file=sys.stderr)
                return 2
            print(render_task(tasks[0], full=True))
            return 0
        if args.markdown:
            print(render_tasks_markdown(tasks))
            return 0
        for t in tasks:
            print(render_task(t))
        print(f"\n{len(tasks)} tasks, {sum(t.ground_truth.verified for t in tasks)} verified")
        return 0

    if args.cmd == "validate":
        rc = 0
        all_tasks = load_tasks(include_unverified=True)
        verified = [t for t in all_tasks if t.ground_truth.verified]
        print(f"tasks: {len(all_tasks)} parsed, {len(verified)} ground-truth verified")
        by_dom: dict[str, list] = {}
        for t in all_tasks:
            by_dom.setdefault(t.domain, []).append(t)
        for dom, ts in sorted(by_dom.items()):
            dims = {}
            for t in ts:
                dims[t.dimension.value] = dims.get(t.dimension.value, 0) + 1
            unv = [t.id for t in ts if not t.ground_truth.verified]
            print(f"  {dom}: {dims}" + (f"  UNVERIFIED: {unv}" if unv else ""))
        for s in registry.servers:
            problems = s.pinned()
            roles_missing = sorted({t.expect_first_role for t in all_tasks
                                    if t.domain == s.domain and t.expect_first_role
                                    and not s.tools_for_role(t.expect_first_role)})
            missing = s.missing_env()
            if missing:
                problems = [*problems, f"env not set: {missing}"]
            flag = "OK " if not problems else "NOT PUBLISHABLE"
            print(f"  server {s.id:16s} {flag} {s.version_tested or '?':12s} "
                  f"tested {s.date_tested or '?'}  notified={s.maintainer_notified}"
                  + (f"  problems={problems}" if problems else "")
                  + (f"  unmapped roles={roles_missing}" if roles_missing else ""))
            rc |= bool(problems)
        for d in registry.distractors:
            problems = d.pinned()
            print(f"  distractor {d.id:12s} {'OK' if not problems else problems}")
        return rc

    if args.cmd == "tools":
        from .agent import Toolset

        server = registry.get(args.server)
        dist = [] if args.no_distractors else registry.distractors_for(server)

        async def go() -> None:
            async with Toolset(server, dist, Path(".lsbench-tools.jsonl")) as ts:
                for t in ts.tools:
                    print(f"{t['name']}\n    {t['description'].strip().splitlines()[0][:140] if t['description'] else ''}")
                print(f"\n{len(ts.tools)} tools")

        asyncio.run(go())
        Path(".lsbench-tools.jsonl").unlink(missing_ok=True)
        return 0

    # run / compare
    from .harness import compare_servers
    from .model import AnthropicModel

    cfg = RunConfig(model=args.model, effort=args.effort, repeats=args.repeats, max_usd=args.max_usd)
    tasks = load_tasks(dimension=args.dimension, include_unverified=args.include_unverified)
    if not tasks:
        print("no tasks selected (are any ground-truth verified?)", file=sys.stderr)
        return 2
    key = None
    if args.key:
        from provmcp.keys import load_private

        key = load_private(args.key)
    model = AnthropicModel()

    ids = [args.server] if args.cmd == "run" else args.servers
    reports = asyncio.run(compare_servers(
        model, registry, ids, tasks, cfg,
        distractors=not args.no_distractors, out_dir=Path(args.out), signing_key=key,
    ))
    for r in reports:
        print("\n" + render_run(r))
        print(f"\n  written to {r['run_dir']}")
    if len(reports) > 1:
        print("\n" + render_compare(reports))
    if any(r["unstable"] or r["fairness_problems"] or r.get("budget_exhausted") for r in reports):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
