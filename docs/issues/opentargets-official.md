**Repo:** https://github.com/opentargets/open-targets-platform-mcp
**Title:** Observations from a fixed-model benchmark run: schema payload size, and `target: null` for unknown IDs

Not bugs — two observations from running the server under a benchmark, in case they're useful.

## Schema-discovery payload size

`get_open_targets_graphql_schema` with one or two categories returned 57–77k characters per call; `get_type_dependencies` for `Target` returned 107k. With Claude Sonnet 5 that is 15–19k tokens per call, and a "what drugs target EGFR" question took 6–10 tool rounds and ~290k tokens (mostly cache reads) as the model paged through it. Correctness was 12/12 and routing 12/12, so the design works; the cost is on the budget side. A `fields:`-level filter, or a compact rendering (names and types without descriptions) as an option, would cut most of it.

## Null for a nonexistent ID

`query { target(ensemblId: "ENSG99999999999") { id } }` via `query_open_targets_graphql` returns `{"target": null}` with no error. Correct GraphQL, and Sonnet handled it every time — but a weaker model has nothing telling it the ID is invalid rather than merely empty. A note in the tool description ("null means the ID does not exist") would be enough.

## Context

Version tested: 2026.7.1 (742f7ef), 2026-09-20, data release 26.06. Part of lsbench, a benchmark for life-sciences MCP servers with the model held fixed (public repo coming; I'll link it here); this server is its positive control and it scored as one. Transcripts available.
