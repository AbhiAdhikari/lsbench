# lsbench

A benchmark for life-sciences MCP **servers**. The model is held fixed; the server
varies.

> Status: **first series published** — three servers × 25 tasks × 3 repeats
> on Claude Sonnet 5, 2026-09-20, $11 including one superseded run. Results and the full argument are in
> [docs/results-2026-09-20.md](docs/results-2026-09-20.md); artifacts in
> [`published/`](published/). Maintainers were notified before publication
> ([issues](docs/issues/README.md)). The headline: **routing saturated on every
> server** — the original thesis is not supported at this tier — and the
> servers separate on error handling and response size instead.

## Why this doesn't already exist

There are many MCP benchmarks — MCP-Bench, MCP-Universe, MCP-Atlas, MCP-AgentBench,
MCP-SafetyBench, MCP-RADAR, MCPToolBench++. Nearly all of them hold the server
fixed and vary the model: they answer *which model is better at tool use*, across
domains like finance, travel, browser automation, and repository management.

One benchmark, modelscope/MCPBench, does the inverse — scoring servers on accuracy,
latency, and token consumption under a fixed LLM and agent configuration. It covers
Web Search, Database Query, and GAIA.

Nobody has done life sciences, and nobody scores the dimensions that separate a
designed server from a generated wrapper.

## The controlled comparison

| Server | Design | Domain |
|---|---|---|
| [Open Targets Platform MCP](https://github.com/opentargets/platform-mcp) `2026.7.1` (official, built with Anthropic) | **5 tools.** Schema discovery → type dependencies → GraphQL. The model composes queries. | target–disease |
| [nickzren/opentargets-mcp](https://github.com/nickzren/opentargets-mcp) `0.5.0` | **68 tools**, one per Platform endpoint. Same upstream API. | target–disease |
| [MCPmed/GEOmcp](https://github.com/MCPmed/GEOmcp) `0.1.2` | **6 search tools**, one per GEO database, mirroring E-utilities. No lookup-by-accession. | gene expression |

Two servers over the *same* Open Targets API with opposite designs: that is the
experiment. GEO is the second domain. Distractors loaded alongside every run:
`clinicaltrials-mcp 0.1.1` (5 tools) and `pubmedmcp 0.1.4` (1 tool).

## Dimensions

| Dimension | Question | What it's really measuring | Scored from |
|---|---|---|---|
| **routing** | Right tool first, unprompted, with distractors loaded? | Tool descriptions | the first `tool_use` in the transcript, before any recovery masks it |
| **recovery** | After an error, does the model recover alone? | Error message design | a surfaced error + claims on the answer + (optionally) a retry with the bad constraint dropped |
| **budget** | Bounded responses, truncation disclosed? | Summarization and pagination | measured tokens per tool result vs a cap; disclosure regex on the *server's* text |
| correctness | Is the biology right? | Metadata resolution | claims vs hand-verified ground truth |
| latency | Wall clock to answer | Upstream call structure, caching | measured on every task |
| cost | Total trajectory tokens | Verbosity, forced round trips | API usage on every task |

Routing, recovery, and budget lead the report. They're the parts nobody tests and
the parts a wrapper generator cannot produce.

Two things the scorer refuses to hide:

- **Silent failure.** A recovery task where the server answers a bad input with a
  clean success (GEOmcp without its email config returns `"Empty id list"` as a
  success) is *unexercised*, not passed — reported separately as its own finding.
- **Unsupported answers.** A correctness claim whose value appears in the answer
  but in no tool result gets no credit and is flagged. The model may have known
  it from pretraining, or may be mentioning it without asserting it ("no subtype
  field was returned, e.g. ER+/HER2+"); either way the server didn't deliver it.

## Method

- **Claims-based scoring**, after MCP-Atlas: each task declares independent
  verifiable claims (`exact`, `numeric`, `contains`, `any`, `absent`). Objective
  partial credit. `fuzzy` claims exist in the schema but are **not scored** — no
  judge is wired in — and the fuzzy fraction is reported with every score.
- **Ground truth is verified against the primary source, not through any MCP
  server**, and the verification method, date, and data release are recorded in
  the task file. 25 tasks, all verified 2026-09-20 (NCBI E-utilities + GEO SOFT;
  Open Targets GraphQL at release 26.06). An unverified task is never scored.
- **Roles, not tool names.** A routing task says `expect_first_role: series_lookup`;
  `servers.yaml` maps that role to each server's real tools, publicly. A server
  with no tool for a role is *unexercised* on that task, and the gap is reported.
- **Fixed model and effort; repetition instead of a seed.** Current Claude models
  reject `temperature` and have no seed. Every task runs N times; a task that
  passes in some runs and fails in others is an **unstable** task, reported as a
  failure. A suite that scores 28/30 once and 19/30 next run has measured nothing.
- **Distractor servers** loaded alongside the server under test. Tool discovery
  under distraction is the primary failure mode prior work identifies.
- **Per-dimension reporting only.** No headline number exists in the code.

## Every run is provenance-recorded

The harness reaches every server through [provmcp](https://github.com/AbhiAdhikari/provmcp), in-process. Every
tool call in a benchmark run lands in a hash-chained, signable ledger with the
upstream data release at the time (`GEO gds Build260919-1942.1`, `OT 26.06`). A
published score ships with `provenance.jsonl`; a reviewer can run
`provmcp replay` on it later and see which upstream data has moved since. The
provenance log is the instrumentation the benchmark reads.

## Fairness

This is published work about other people's code.

- Exact versions pinned, dates recorded in `src/lsbench/servers.yaml`; `lsbench
  validate` refuses to call an unpinned server publishable
- Issues filed with reproductions **before** scores are published
- Maintainer responses recorded alongside the scores
- The official Open Targets server is the positive control — if the designed
  server doesn't outscore the wrapper, the thesis is wrong and that gets published too

Series 2026-09-20, Claude Sonnet 5, strict scores (unstable task → 0):

| dimension | otp-official | otp-community | geomcp |
|---|---|---|---|
| routing | 100% n=12 | 100% n=12 | 100% n=15 |
| recovery | 100% n=3 | 0% (raw 67%) n=3 | 100% n=4 |
| budget | 25% n=6 | 25% n=6 | 0% (raw 8%) n=6 |
| correctness | 100% n=12 | 100% n=12 | 86% n=11 |
| tool calls that errored | 11% | 48% | 16% |
| tokens/task | 117k | 287k | 42k |

Read [docs/results-2026-09-20.md](docs/results-2026-09-20.md) before quoting
any of these: the interesting content is in the transcripts, not the table.

Findings from setup (all from `lsbench tools` / `validate`):

- `geo-mcp 0.1.2`, `clinicaltrials-mcp 0.1.1`, `pubmedmcp 0.1.4` all crash on the
  2.x Python MCP SDK; each needs `uvx --with "mcp<2"`.
- `geo-mcp` without `GEOMCP_EMAIL` returns every search as an empty success. The
  benchmark sets the email; the swallowed error is still a finding.
- `opentargets-mcp 0.5.0` exposes `get_target_known_drugs`, named for a field
  Open Targets removed in release 26.06 (`knownDrugs` → `drugAndClinicalCandidates`).
  Whether it still works is what `ot-recover-002` measures.

## What the benchmark is

The benchmark is four files, all in this repo and all reviewable without running
anything:

| File | What it fixes |
|---|---|
| `tasks/<domain>.yaml` | the prompts, the claims, the ground truth and how it was verified |
| `tasks/_scoring.yaml` | the disclosure and error patterns, and shared claim phrase lists — SHA-256 recorded in every report |
| `src/lsbench/servers.yaml` | which server versions, and the role→tool mapping per server |
| `src/lsbench/config.py` | the model, effort, turn cap, repeats |

`lsbench tasks` prints every task with what it's scored on and how its answer was
verified; `--id` shows one in full with the recorded ground-truth values;
`--markdown` renders the table in [docs/tasks.md](docs/tasks.md). A score is a
function of these four files and the model; change any one and it's a new
report series.

## Usage

```bash
uv venv && uv pip install -e ".[dev]"      # pulls provmcp from PyPI
export ANTHROPIC_API_KEY=...               # or `ant auth login`
export GEOMCP_EMAIL=you@example.org        # NCBI E-utilities contact, for geo-mcp

lsbench validate                           # tasks parse, ground truth flagged, pins checked
lsbench tasks [--id geo-correct-003]       # read the benchmark
lsbench tools --server otp-official        # the exact tool surface the model will see
lsbench run --server otp-official --repeats 3
lsbench compare otp-official otp-community # the table for the write-up
lsbench show results/otp-official/<ts>/report.json
lsbench rescore results/*/<ts>              # new rules, same transcripts, no API calls
```

Defaults: `claude-opus-5`, adaptive thinking, effort `high`, 3 repeats. Override
with `--model` / `--effort` / `--repeats`; the values are written into every
report and a different model is a different report series.

Cost estimate before you run: 25 tasks × 3 repeats × ~3 servers ≈ 225
trajectories; a few thousand tokens each is on the order of a million tokens.
Check `report.json` → `measured.tokens_mean` after one server before running all.

## Output

```
results/<server>/<timestamp>/
  report.json         config, pinned server, per-dimension aggregates, instability, fairness
  results.jsonl       one TaskResult per task per repeat, with per-claim outcomes
  trajectories.jsonl  every call: arguments, raw result, is_error, measured tokens, latency
  provenance.jsonl    provmcp ledger — replayable
```

## Known limits

- **No judge.** Fuzzy claims are unscored. Every current task is programmatic.
- **Disclosure is a regex.** "truncated", "showing first N of M", "cursor", and
  similar. A server that discloses in a novel phrasing is under-scored; the
  patterns are in `tasks/_scoring.yaml`, their hash is in every report, and
  every raw result is in the transcript, so a disputed call can be re-scored.
- **Per-result token counts are derived** from API usage deltas between turns
  (exact in aggregate, split by character count when one turn has several
  results). Raw character counts are recorded alongside.
- **Tool names carry the server id** (`otp-official__search_entities`). That is
  what real clients show a model, but it does leak "clinicaltrials" into the
  distractor-routing tasks. Same for every server, so fair — but not blind.
- **Three servers, two domains, 25 tasks.** Enough for a controlled comparison,
  not for a leaderboard. Adding a server is a `servers.yaml` entry and a role map.

## License

MIT.
