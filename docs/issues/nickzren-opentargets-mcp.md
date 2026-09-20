**Repo:** https://github.com/nickzren/opentargets-mcp
**Title:** `search_entities` and `get_target_known_drugs` fail against Open Targets release 26.06; error text reaching the client is empty

## What happens

Against the current Platform API (data release 26.06, checked 2026-09-20), two tools return an error on every call:

- `search_entities` — e.g. `{"query_string": "BRCA1", "entity_names": ["target"]}`
- `get_target_known_drugs` — e.g. `{"ensembl_id": "ENSG00000146648"}`

The upstream response is HTTP 400. The tool result delivered to the MCP client is only:

```
Error calling tool 'search_entities'
```

The GraphQL error message (which says exactly which field is gone) is written to the server's stderr and not returned to the client, so an agent has nothing to act on.

## Why

Release 26.06 renamed `Target.knownDrugs` → `Target.drugAndClinicalCandidates` (and, inside it, `clinicalStage` → `maxClinicalStage`). `get_target_known_drugs` queries the old field. `search_entities` appears to select a field the search type no longer has; `map_ids` works, so the search endpoint itself is fine.

Verified directly, no MCP involved:

```bash
curl -s https://api.platform.opentargets.org/api/v4/graphql \
  -H 'content-type: application/json' \
  -d '{"query":"{ target(ensemblId:\"ENSG00000146648\") { knownDrugs { count } } }"}'
# → {"errors":[{"message":"Cannot query field 'knownDrugs' on type 'Target'. ..."}]}

curl -s https://api.platform.opentargets.org/api/v4/graphql \
  -H 'content-type: application/json' \
  -d '{"query":"{ target(ensemblId:\"ENSG00000146648\") { drugAndClinicalCandidates { count } } }"}'
# → {"data":{"target":{"drugAndClinicalCandidates":{"count":82}}}}
```

## Reproduce with the server

```bash
uvx --python 3.12 opentargets-mcp==0.5.0
# then, from any MCP client:
#   search_entities {"query_string": "BRCA1", "entity_names": ["target"]}
#   get_target_known_drugs {"ensembl_id": "ENSG00000146648"}
```

## Suggestions

1. Return the GraphQL `errors[].message` in the tool result (ideally with `isError: true`). In a benchmark run on 2026-09-20, Claude Sonnet 5 recovered from these failures every time — but only by dropping to `graphql_query` and introspecting `__type(name:"Target")`, 9–12 tool rounds per task. With the message in-band it would be one.
2. Update the two queries for 26.06, or derive field names from the schema at startup.

## Context

Found while running [lsbench](…), a benchmark for life-sciences MCP servers with the model held fixed. Version tested: 0.5.0. Full transcripts are in the published run; happy to share anything else. The server otherwise did well — 12/12 on routing and correctness in the same run.
