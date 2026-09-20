"""Render reports. Per dimension, never a single number.

The headline dimensions (routing, recovery, budget) come first because they
are the ones that separate a designed server from a generated wrapper.
"""

from __future__ import annotations

import math

from .dimensions import report_order
from .tasks import Task


def _pct(x: float) -> str:
    return "  n/a " if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:6.1%}"


def render_run(report: dict) -> str:
    s = report["server"]
    lines = [
        f"=== {s['id']} — {s.get('name') or ''} ({s.get('version_tested') or 'unpinned'}, "
        f"tested {s.get('date_tested') or '?'}) ===",
        f"model {report['config']['model']}  effort {report['config']['effort']}  "
        f"repeats {report['config']['repeats']}  tasks {len(report['tasks'])}  "
        f"distractors {[d['id'] for d in report['distractors']]}",
        "",
        f"  {'dimension':12s} {'strict':>7s} {'raw':>7s}  {'n':>3s}  {'unexer':>6s}  {'unsupp':>6s}  {'cutoff':>6s}",
    ]
    dims = report["dimensions"]
    for dim in report_order():
        row = dims.get(dim.value)
        if not row:
            continue
        lines.append(
            f"  {dim.value:12s} {_pct(row.get('mean_strict', row['mean']))} {_pct(row['mean'])}  "
            f"{row['n']:3d}  {row['unexercised']:6d}  {row['hallucination_rate']:6.1%}  {row['cut_off']:6d}"
        )
    lines.append("  (strict: every run of an unstable task scored 0; raw: as measured; "
                 "unsupp: answers containing a claimed value no tool result contained)")
    m = report.get("measured") or {}
    if m:
        lines += [
            "",
            f"  rounds   mean {m.get('rounds_mean', 0):.1f}  max {m.get('rounds_max', 0)}  cut off {m.get('cut_off', 0)}",
            f"  errors   {m.get('server_error_rate', 0):.0%} of {m.get('server_calls', 0)} calls to the server under test surfaced an error",
            f"  latency  p50 {m['latency_p50_s']:.1f}s  p95 {m['latency_p95_s']:.1f}s",
            f"  cost     mean {m['tokens_mean']:,.0f} tok/task  max {m['tokens_max']:,.0f}  "
            f"largest tool result {m['response_tokens_max']:,.0f} tok",
            f"           uncached in {m.get('input_tokens', 0):,}  cache write {m.get('cache_write', 0):,}  "
            f"cache read {m.get('cache_read', 0):,}  out {m.get('output_tokens', 0):,}",
        ]
        if m.get("estimated_usd") is not None:
            lines.append(f"           ≈ ${m['estimated_usd']:.2f} this run "
                         f"(${m['estimated_usd_per_task']:.3f}/task, list price, estimate)")
    if report.get("fuzzy_fraction"):
        lines.append(f"  fuzzy claims (unscored): {report['fuzzy_fraction']:.0%} of task claims")
    if report.get("budget_exhausted"):
        lines += ["", "  PARTIAL RUN — stopped at --max-usd; not publishable"]
    if report.get("unstable"):
        lines += ["", "  UNSTABLE — scored as failures, fix before publishing:"]
        lines += [f"    - {u}" for u in report["unstable"]]
    if report.get("fairness_problems"):
        lines += ["", "  NOT PUBLISHABLE — fairness rules unmet:"]
        lines += [f"    - {p}" for p in report["fairness_problems"]]
    return "\n".join(lines)


def render_compare(reports: list[dict]) -> str:
    """Side-by-side markdown table. This is the output that goes in the write-up."""
    ids = [r["server"]["id"] for r in reports]
    head = "| dimension | " + " | ".join(ids) + " |"
    sep = "|---|" + "---|" * len(ids)
    rows = [head, sep]
    for dim in report_order():
        if not any(dim.value in r["dimensions"] for r in reports):
            continue   # latency and cost are measured, not task dimensions
        cells = []
        for r in reports:
            row = r["dimensions"].get(dim.value)
            if not row:
                cells.append("—")
                continue
            strict, raw = row.get("mean_strict", row["mean"]), row["mean"]
            cell = _pct(strict).strip()
            if strict != raw and not (math.isnan(strict) and math.isnan(raw)):
                cell += f" (raw {_pct(raw).strip()})"
            cells.append(cell + f" n={row['n']}")
        rows.append(f"| {dim.value} | " + " | ".join(cells) + " |")
    rows.append("| unexercised | " + " | ".join(
        str(sum(d["unexercised"] for d in r["dimensions"].values())) for r in reports) + " |")
    rows.append("| harness failures | " + " | ".join(
        str(sum(d.get("failures", 0) for d in r["dimensions"].values())) for r in reports) + " |")
    rows.append("| tool errors | " + " | ".join(
        f"{r['measured'].get('server_error_rate', 0):.0%} of {r['measured'].get('server_calls', 0)}"
        if r.get("measured") else "—" for r in reports) + " |")
    rows.append("| rounds/task | " + " | ".join(
        f"{r['measured'].get('rounds_mean', 0):.1f} (max {r['measured'].get('rounds_max', 0)}, {r['measured'].get('cut_off', 0)} cut off)"
        if r.get("measured") else "—" for r in reports) + " |")
    rows.append("| unsupported answers | " + " | ".join(
        f"{max(d['hallucination_rate'] for d in r['dimensions'].values()):.0%}" if r["dimensions"] else "—"
        for r in reports) + " |")
    rows.append("| latency p50 | " + " | ".join(
        f"{r['measured']['latency_p50_s']:.1f}s" if r.get("measured") else "—" for r in reports) + " |")
    rows.append("| tokens/task | " + " | ".join(
        f"{r['measured']['tokens_mean']:,.0f}" if r.get("measured") else "—" for r in reports) + " |")
    rows.append("| est. $/task | " + " | ".join(
        f"{r['measured']['estimated_usd_per_task']:.3f}"
        if r.get("measured") and r["measured"].get("estimated_usd_per_task") is not None else "—"
        for r in reports) + " |")
    rows.append("| unstable tasks | " + " | ".join(str(len(r.get("unstable", []))) for r in reports) + " |")
    rows.append("| version | " + " | ".join(r["server"].get("version_tested") or "?" for r in reports) + " |")
    return "\n".join(rows)


def _claim_line(c) -> str:
    exp = c.expected
    if isinstance(exp, list) and len(exp) > 4:
        exp = [*exp[:4], f"… +{len(exp) - 4}"]
    tol = f" ±{c.tolerance:g}" if c.tolerance else ""
    return f"{c.id}: {c.kind} {exp!r}{tol}"


def render_task(t: Task, full: bool = False) -> str:
    """One task, human-readable. This is what a reviewer reads to decide whether
    the benchmark measures what it claims to."""
    gt = t.ground_truth
    head = f"{t.id}  [{t.domain} / {t.dimension.value}]  {'verified' if gt.verified else 'UNVERIFIED'}"
    lines = [head, f"  prompt: {t.prompt}"]
    if t.expect_first_role:
        lines.append(f"  scored on: first tool call is role {t.expect_first_role!r} (mapped per server)")
    if t.expect_server:
        lines.append(f"  scored on: first tool call is NOT the server under test (expected {t.expect_server})")
    if t.max_response_tokens is not None:
        lines.append(f"  scored on: every tool result <= {t.max_response_tokens} tokens"
                     + ("; truncation must be disclosed" if t.expect_truncation_disclosed else ""))
    if t.dimension.value == "recovery":
        lines.append("  scored on: a surfaced tool error, then the claims below"
                     + (f"; a later call must drop {t.expect_retry_without!r}" if t.expect_retry_without else ""))
    for c in t.claims:
        lines.append(f"  claim  {_claim_line(c)}")
    if gt.method:
        lines.append(f"  verified: {gt.method}  ({gt.date}{', ' + gt.release if gt.release else ''})")
    if full:
        if gt.values:
            lines.append("  ground truth values:")
            for k, v in gt.values.items():
                lines.append(f"    {k}: {v}")
        if t.notes:
            lines.append("  notes: " + " ".join(t.notes.split()))
    return "\n".join(lines)


def render_tasks_markdown(tasks: list[Task]) -> str:
    rows = ["| id | dimension | prompt | scored on | verified against |", "|---|---|---|---|---|"]
    for t in tasks:
        if t.expect_first_role:
            scored = f"first call is role `{t.expect_first_role}`"
        elif t.expect_server:
            scored = f"first call is not the server under test (→ {t.expect_server})"
        elif t.max_response_tokens is not None:
            scored = f"results ≤ {t.max_response_tokens} tok" + (", truncation disclosed" if t.expect_truncation_disclosed else "")
        else:
            scored = "; ".join(f"{c.kind} {c.id}" for c in t.claims)
            if t.dimension.value == "recovery":
                scored = "error surfaced; " + scored
        rows.append(f"| {t.id} | {t.dimension.value} | {t.prompt} | {scored} | {t.ground_truth.method or '—'} |")
    return "\n".join(rows)
