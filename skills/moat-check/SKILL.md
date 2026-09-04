---
name: moat-check
description: Build moat queries, retrieve time-bounded evidence, and assess claimed powers, VRIO strength, and erosion risks for a startup proposal. Use when an independent U-check is needed.
---

# Moat Check

Run the proposal framework's independent U-check directly from the proposal
profile. Apply one uniform moat methodology without company-category gates.

## Workflow

1. Extract the documented values into `placeholders.json`, then run
   `scripts/build_queries.py`. Resolve any `<null:name>` query placeholders.
2. Resolve a valid founding year and run `scripts/run_tavily.py`.
3. Read `reference/u_check.md`, interpret the retrieved evidence, and write
   `u_check.json`.
4. Run `scripts/validate_u_schema.py` before accepting the output.
5. Treat sparse evidence and an unknown founding year according to the
   documented fail/warn rules; never manufacture evidence.

Read `reference/workflow.md` for the standalone input/output sequence and
`reference/u_check.md` for VRIO scoring, schema, and edge cases. Query
placeholders are under `examples/`.

## Requirements

Python 3.10+, `tavily-python`, and `TAVILY_API_KEY` for retrieval.
