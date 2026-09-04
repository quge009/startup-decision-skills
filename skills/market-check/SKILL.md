---
name: market-check
description: Build market queries, retrieve time-bounded evidence, and assess TAM, SAM, SOM headroom, and competitive forces for a startup proposal. Use when an independent M-check is needed.
---

# Market Check

Run the proposal framework's independent M-check directly from the proposal
profile. Company archetypes are not part of this workflow.

## Workflow

1. Extract the documented values into `placeholders.json`, then run
   `scripts/build_queries.py`. Resolve any `<null:name>` query placeholders.
2. Resolve a valid founding year and run `scripts/run_tavily.py`.
   Retrieval is restricted to `[founding_year - 3 years, founding_year]`.
3. Read `reference/m_check.md`, interpret the retrieved evidence, and write
   `m_check.json`.
4. Run `scripts/validate_m_schema.py` before accepting the output.
5. Treat missing keys, sparse evidence, and unknown founding year according to
   the fail/warn rules; never manufacture evidence.

Read `reference/workflow.md` for the standalone input/output sequence and
`reference/m_check.md` for thresholds, schema, and edge cases. Query
placeholders and an example are under `examples/`.

## Requirements

Python 3.10+, `tavily-python`, and `TAVILY_API_KEY` for retrieval.
