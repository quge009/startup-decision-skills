---
name: event-evidence-collector
description: Collect source-grounded funding, exposure, and investor-company interface event claims into per-company JSON caches using web search and structured LLM extraction. Use before event-chain-builder when constructing comparable public-evidence event histories.
---

# Event Evidence Collector

Collect three non-overlapping evidence families without writing analytical
Parquet tables: funding outcomes, company exposures, and investor-company
interfaces.

## Workflow

1. Supply a company entity Parquet containing `company_id`, `canonical_name`, and
   `slug` through `--entity-path`.
2. Set `TAVILY_API_KEY` and `OPENROUTER_API_KEY`. Optionally set
   `OPENROUTER_MODEL` and `OPENROUTER_URL`. If using `--search-backend serper`,
   set `SERPER_API_KEY` instead of the Tavily key.
3. Run the relevant `collect_*_events.py` with `--slugs` or `--limit` and an
   explicit `--cache-dir`.
4. Inspect source URLs and low-confidence extractions. Re-run a company with
   `--force` only when replacement is intended.
5. Pass the caches to `event-chain-builder`. Collection and materialization stay
   separate so a partial cache cannot silently shrink an existing table.

The schemas under `schemas/` are authoritative for event-type definitions.
Search results are evidence candidates, not truth: retain URLs, reject semantic
drift, and never fabricate missing dates, amounts, parties, or outcomes.

## Requirements

Python 3.10+, `pyarrow`, `tavily-python`, Tavily API access, and an
OpenAI-compatible chat-completions endpoint. Serper is an optional HTTP backend.
