# Superseded runs

Kept so the record shows what was found and why it was replaced. Nothing here
is a published score.

- `2026-09-20-sonnet-5-geomcp-disk-limited/` — the first geomcp series. The
  test machine had 4.7 GB free and geo-mcp refuses downloads below 10 GB at
  its defaults, so every `download_geo_data` call failed for environmental
  reasons (21 of 48 errors). Replaced by a re-run with `--max-file-size-mb 500`
  the same day. See docs/results-2026-09-20.md, *Caveats*.
