**Repo:** https://github.com/MCPmed/GEOmcp
**Title:** Three things an agent can't see: missing NCBI email becomes an empty success, no by-accession lookup, and `mcp>=2` crashes at import

## 1. Missing `GEOMCP_EMAIL` is swallowed

Without an email configured, every search tool returns

```json
{"esummaryresult": ["Empty id list - nothing todo"]}
```

as a normal (non-error) result. `geo_profiles.search_geo` catches the `_require_email()` exception and returns the empty shape. An agent reads that as "no results for your query", not "the server is misconfigured", and never retries. Suggest raising at startup, or returning the tool result with `isError: true` and the actual message.

## 2. No lookup-by-accession

The tool surface has `search_geo_series(term)`, `search_geo_samples(term)` etc. but nothing that takes a GSE/GSM accession and returns the record. Models manage — `search_geo_series {"term": "GSE176078"}` finds it — but `search_geo_series {"term": "GSE239938"}` returned empty while `search_geo {"term": "GSE239938[ACCN]", "record_types": ["GSE"]}` worked, so success depends on the model guessing E-utilities field syntax. `esummary` for a GSM also carries no `characteristics`, so sample metadata (tissue, subtype) is not reachable without a SOFT download.

## 3. Crash on the current Python MCP SDK

```
$ uvx geo-mcp==0.1.2
AttributeError: 'Server' object has no attribute 'list_tools'
```

`mcp` 2.x removed the decorator API. `uvx --with "mcp<2" geo-mcp` works. Pinning `mcp<2` in `pyproject.toml` (or migrating) would fix new installs.

## Also observed (lower priority)

- `search_geo_series` / `search_geo_samples` return the raw esummary payload — 33k+ tokens for a 7,186-sample series — with no total count or truncation marker. `search_geo` does report `total_count`. Consistency would help agents know when they've seen everything.
- `download_geo_data` requires free disk ≥ 2 × `max_file_size_mb` — 10 GB at the default — and reports `insufficient disk space to continue` otherwise. Worth mentioning in the README; a 500 MB default would suit most callers.

## Context

Found while running lsbench, a benchmark for life-sciences MCP servers with the model held fixed (public repo coming; I'll link it here, and can share the transcripts now on request). Version tested: 0.1.2, 2026-09-20. Routing was 15/15 in that run — the tool descriptions do their job; the items above are about what comes back.
