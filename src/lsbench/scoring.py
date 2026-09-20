"""Claims-based scoring.

Borrowed from MCP-Atlas: instead of asking an LLM to holistically judge an answer,
each task declares independent verifiable claims the answer must contain. Score is
the fraction present. Objective partial credit, no judge for the parts a string or
numeric comparison can settle.

Claims marked `fuzzy` are NOT scored by this module — there is no judge wired in
yet. They are excluded from the denominator and the fuzzy fraction is reported
alongside every score, so a reader knows how much of the task the number does
not cover.

Every dimension is scored from the trajectory, not from the model's self-report:
the first tool call is read off the transcript, the response size is measured,
an error is what the server actually returned.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field

from .agent import Trajectory
from .dimensions import Dimension
from .rules import default_rules
from .servers import ServerSpec
from .tasks import Claim, Task


@dataclass
class TaskResult:
    task_id: str
    dimension: str
    claims_passed: int
    claims_total: int
    exercised: bool = True           # False: the condition the task measures never arose
    first_tool: str | None = None
    calls: int = 0
    rounds: int = 0
    server_calls: int = 0            # calls to the server under test
    server_errors: int = 0           # ...that surfaced an error (flagged or error-like)
    response_tokens_max: int = 0     # largest single tool result the model read
    total_tokens: int = 0
    input_tokens: int = 0            # uncached
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    error_surfaced: bool | None = None
    truncation_disclosed: bool | None = None
    hallucinated: bool = False       # asserted content with no tool support
    fuzzy_skipped: int = 0
    cut_off: bool = False
    failure: str = ""
    claim_results: dict[str, bool] = field(default_factory=dict)
    notes: str = ""

    @property
    def score(self) -> float:
        if self.claims_total == 0:
            return 0.0
        return self.claims_passed / self.claims_total


# ---------- claims ----------

def check_claim(claim: Claim, answer: str) -> bool:
    """Programmatic claim checks. Raises for fuzzy claims — those need a judge."""
    text = answer.lower()

    if claim.kind == "exact":
        return str(claim.expected).lower() in text

    if claim.kind == "contains":
        expected = claim.expected if isinstance(claim.expected, list) else [claim.expected]
        return all(str(t).lower() in text for t in expected)

    if claim.kind == "any":
        expected = claim.expected if isinstance(claim.expected, list) else [claim.expected]
        return any(str(t).lower() in text for t in expected)

    if claim.kind == "absent":
        expected = claim.expected if isinstance(claim.expected, list) else [claim.expected]
        return not any(str(t).lower() in text for t in expected)

    if claim.kind == "numeric":
        want = float(claim.expected)
        for match in re.findall(r"-?\d+(?:\.\d+)?", answer.replace(",", "")):
            if abs(float(match) - want) <= claim.tolerance:
                return True
        return False

    if claim.kind == "fuzzy":
        raise ValueError(f"claim {claim.id} is fuzzy; route to a judge, not check_claim")

    raise ValueError(f"unknown claim kind: {claim.kind}")


_PREFIXED_ID = re.compile(r"^([A-Za-z]{2,6})(\d{4,})$")


def _support_variants(value: str) -> list[str]:
    """Forms a tool result may carry an identifier in. GEO esummary returns
    `"gpl": "18573"` for GPL18573 and `"gse": "176078"` for GSE176078; a model
    that writes the prefixed accession has not invented anything."""
    v = value.lower()
    out = [v]
    m = _PREFIXED_ID.match(value)
    if m:
        out.append(m.group(2))
    return out


def _supported_by_tools(claim: Claim, traj: Trajectory) -> bool:
    """Did any tool result contain the value the claim asserts? If the answer
    passes a claim no tool output supports, the model made it up (or knew it
    from pretraining — indistinguishable here, and equally unearned)."""
    corpus = "\n".join(c.result_text for c in traj.calls).lower()
    if claim.kind == "numeric":
        return str(claim.expected) in corpus.replace(",", "") or (
            float(claim.expected).is_integer() and str(int(float(claim.expected))) in corpus.replace(",", "")
        )
    if claim.kind in ("exact", "contains"):
        expected = claim.expected if isinstance(claim.expected, list) else [claim.expected]
        return all(any(v in corpus for v in _support_variants(str(t))) for t in expected)
    return True


def _score_claims(
    task: Task, traj: Trajectory, *, check_support: bool = True
) -> tuple[int, int, int, dict[str, bool], bool]:
    """check_support: flag a passed fact claim as hallucinated when no tool
    result contains it. On for correctness; off for recovery, where a claim
    like 'acknowledges the year' is about the answer, not a retrieved fact."""
    passed, total, skipped, results, halluc = 0, 0, 0, {}, False
    for claim in task.claims:
        if claim.fuzzy:
            skipped += 1
            continue
        total += 1
        ok = check_claim(claim, traj.final_text)
        if ok and check_support and claim.kind in ("exact", "numeric", "contains") \
                and not _supported_by_tools(claim, traj):
            # The string is in the answer but in no tool output. Either the model
            # invented it, or the answer mentions it without asserting it ("no
            # subtype field was returned, e.g. ER+/HER2+"). Either way the server
            # did not deliver it: no credit, and flagged.
            ok = False
            halluc = True
        results[claim.id] = ok
        if ok:
            passed += 1
        elif claim.kind == "absent":
            halluc = True   # it asserted the thing it was told not to invent
    return passed, total, skipped, results, halluc


# ---------- per-dimension ----------

def _base(task: Task, traj: Trajectory) -> TaskResult:
    fc = traj.first_call
    mine = [c for c in traj.calls if c.server == traj.server_id]
    return TaskResult(
        task_id=task.id, dimension=task.dimension.value, claims_passed=0, claims_total=0,
        first_tool=fc.name if fc else None, calls=len(traj.calls), rounds=traj.rounds,
        server_calls=len(mine), server_errors=sum(c.surfaced_error for c in mine),
        response_tokens_max=max((c.tokens or c.result_chars // 4 for c in traj.calls), default=0),
        total_tokens=traj.total_tokens, latency_s=traj.latency_s,
        input_tokens=traj.usage.input_tokens,
        cache_write_tokens=traj.usage.cache_creation_input_tokens,
        cache_read_tokens=traj.usage.cache_read_input_tokens,
        output_tokens=traj.usage.output_tokens,
        error_surfaced=any(c.surfaced_error for c in traj.calls) if traj.calls else None,
        cut_off=traj.cut_off, failure=traj.failure,
    )


def score_routing(task: Task, traj: Trajectory, server: ServerSpec) -> TaskResult:
    r = _base(task, traj)
    r.claims_total = 1
    fc = traj.first_call
    if fc is None:
        r.notes = "no tool called"
        return r
    if task.expect_server:
        # Distractor routing: the server under test must not be the first call.
        ok = fc.server != server.id
        r.claims_passed = int(ok)
        r.claim_results["not_under_test_first"] = ok
        r.notes = f"first call went to {fc.server}; expected {task.expect_server}"
        return r
    expected = server.tools_for_role(task.expect_first_role or "")
    if not expected:
        r.exercised = False
        r.notes = f"server has no tool mapped to role {task.expect_first_role!r}"
        return r
    ok = fc.server == server.id and fc.tool in expected
    r.claims_passed = int(ok)
    r.claim_results["first_tool"] = ok
    r.notes = f"first {fc.name}; expected one of {expected}"
    return r


def score_budget(task: Task, traj: Trajectory, server: ServerSpec) -> TaskResult:
    r = _base(task, traj)
    mine = [c for c in traj.calls if c.server == server.id]
    if not mine:
        r.claims_total = 1
        r.notes = "server under test never called"
        return r
    cap = task.max_response_tokens or 0
    sizes = [c.tokens if c.tokens is not None else c.result_chars // 4 for c in mine]
    under = max(sizes) <= cap
    r.claim_results["under_cap"] = under
    r.claims_total, r.claims_passed = 1, int(under)
    if task.expect_truncation_disclosed:
        disclosed = any(default_rules().disclosure.search(c.result_text) for c in mine)
        r.truncation_disclosed = disclosed
        r.claim_results["truncation_disclosed"] = disclosed
        r.claims_total += 1
        r.claims_passed += int(disclosed)
    r.notes = f"largest result {max(sizes)} tok vs cap {cap}"
    return r


def score_recovery(task: Task, traj: Trajectory, server: ServerSpec) -> TaskResult:
    r = _base(task, traj)
    err_idx = next((i for i, c in enumerate(traj.calls) if c.surfaced_error), None)
    if task.expect_error and err_idx is None:
        # The server answered a bad input with a success. There is no error
        # message to recover from, so the dimension is not exercised; report it
        # as a silent failure instead, which is its own finding.
        r.exercised = False
        r.notes = "no error surfaced — server returned success for a bad input"
        return r

    passed, total, skipped, results, halluc = _score_claims(task, traj, check_support=False)
    if task.expect_retry_without:
        needle = task.expect_retry_without.lower()
        later = traj.calls[(err_idx or 0) + 1:]
        retried = any(needle not in json.dumps(c.arguments).lower() and c.server == server.id
                      for c in later)
        results["retry_without_" + needle] = retried
        total += 1
        passed += int(retried)
    if total == 0:   # a recovery task with no claims: recovering = answering at all
        total, ok = 1, bool(traj.final_text) and not traj.cut_off
        passed = int(ok)
        results["answered"] = ok
    r.claims_passed, r.claims_total, r.fuzzy_skipped = passed, total, skipped
    r.claim_results, r.hallucinated = results, halluc
    return r


def score_correctness(task: Task, traj: Trajectory, server: ServerSpec) -> TaskResult:
    r = _base(task, traj)
    passed, total, skipped, results, halluc = _score_claims(task, traj)
    r.claims_passed, r.claims_total, r.fuzzy_skipped = passed, total, skipped
    r.claim_results, r.hallucinated = results, halluc
    if not any(c.server == server.id for c in traj.calls):
        r.notes = "server under test never called"
    return r


SCORERS = {
    Dimension.ROUTING: score_routing,
    Dimension.BUDGET: score_budget,
    Dimension.RECOVERY: score_recovery,
    Dimension.CORRECTNESS: score_correctness,
}


def score_task(task: Task, traj: Trajectory, server: ServerSpec) -> TaskResult:
    if traj.failure:
        # Refusal, output-cap truncation, transport failure: the run measured
        # the harness, not the server. Excluded from means and instability,
        # counted in `failures`, never silently a zero.
        r = _base(task, traj)
        r.exercised, r.notes = False, f"harness failure: {traj.failure}"
        return r
    return SCORERS[task.dimension](task, traj, server)


# ---------- aggregation ----------

def aggregate(results: list[TaskResult], unstable_ids: set[str] | None = None) -> dict[str, dict[str, float]]:
    """Per-dimension aggregate. NEVER collapse to a single number in a report.

    `mean` is the raw mean over exercised runs. `mean_strict` scores every run
    of an unstable task as 0 — the number the README promises: a task that
    passes sometimes has not been shown to pass."""
    unstable_ids = unstable_ids or set()
    by_dim: dict[str, list[TaskResult]] = {}
    for r in results:
        by_dim.setdefault(r.dimension, []).append(r)

    out: dict[str, dict[str, float]] = {}
    for dim, rs in by_dim.items():
        scored = [r for r in rs if r.exercised]
        strict = [0.0 if r.task_id in unstable_ids else r.score for r in scored]
        out[dim] = {
            "mean": statistics.mean(r.score for r in scored) if scored else float("nan"),
            "mean_strict": statistics.mean(strict) if strict else float("nan"),
            "unstable_tasks": sorted({r.task_id for r in rs if r.task_id in unstable_ids}),
            "n": len(scored),
            "unexercised": len(rs) - len(scored),
            "hallucination_rate": sum(r.hallucinated for r in rs) / len(rs),
            "median_latency_s": statistics.median(r.latency_s for r in rs),
            "mean_total_tokens": statistics.mean(r.total_tokens for r in rs),
            "fuzzy_skipped": sum(r.fuzzy_skipped for r in rs),
            "cut_off": sum(r.cut_off for r in rs),
            "failures": sum(bool(r.failure) for r in rs),
        }
    return out


def measured(results: list[TaskResult], model: str = "") -> dict[str, float]:
    """Latency and cost across every task, whatever its dimension."""
    if not results:
        return {}
    from .pricing import estimate_usd

    lat = sorted(r.latency_s for r in results)
    tok = [r.total_tokens for r in results]
    classes = {
        "input_tokens": sum(r.input_tokens for r in results),
        "cache_write": sum(r.cache_write_tokens for r in results),
        "cache_read": sum(r.cache_read_tokens for r in results),
        "output_tokens": sum(r.output_tokens for r in results),
    }
    usd = estimate_usd(model, **classes)
    server_calls = sum(r.server_calls for r in results)
    return {
        "n": len(results),
        "rounds_mean": statistics.mean(r.rounds for r in results),
        "rounds_max": max(r.rounds for r in results),
        "cut_off": sum(r.cut_off for r in results),
        "server_calls": server_calls,
        "server_error_rate": (sum(r.server_errors for r in results) / server_calls) if server_calls else 0.0,
        "latency_p50_s": statistics.median(lat),
        "latency_p95_s": lat[min(len(lat) - 1, round(0.95 * (len(lat) - 1)))],
        "tokens_mean": statistics.mean(tok),
        "tokens_max": max(tok),
        "response_tokens_max": max(r.response_tokens_max for r in results),
        **classes,
        "estimated_usd": usd,
        "estimated_usd_per_task": (usd / len(results)) if usd is not None else None,
    }


def instability(runs: list[list[TaskResult]]) -> list[str]:
    """Task ids that pass in some repeats and fail in others.

    These are reported as FAILURES, not partial credit. A suite that scores 28/30
    once and 19/30 the next run has measured nothing.

    Unexercised runs are left out of the comparison: a recovery task where the
    error arose in two runs and not the third is not a flaky score, it is a
    task exercised 2/3 — reported separately in the per-dimension table.
    """
    per_task: dict[str, list[float]] = {}
    for run in runs:
        for r in run:
            if r.exercised:
                per_task.setdefault(r.task_id, []).append(r.score)

    unstable = []
    for task_id, scores in per_task.items():
        if len(set(scores)) > 1:
            lo, hi = min(scores), max(scores)
            unstable.append(f"{task_id} ({lo:.2f}-{hi:.2f} across {len(scores)} runs)")
    return unstable


def unstable_ids(unstable: list[str]) -> set[str]:
    return {u.split(" ", 1)[0] for u in unstable}
