---
name: check-interpreter
description: Build uniform research queries, retrieve time-bounded evidence, and run an archetype-agnostic M-check or U-check on a startup proposal. Use for evidence-grounded market-headroom or moat-strength assessment.
---

# Check Interpreter

Build candidate-specific queries and interpret time-bounded evidence using the
same M/U methodology for every archetype.

## Workflow

1. Extract the seven documented values into `placeholders.json`, then run
   `scripts/build_queries.py`. Resolve any `<null:name>` query placeholders.
2. Resolve a valid founding year and run `scripts/run_tavily.py --check m|u`.
   Retrieval is restricted to `[founding_year - 3 years, founding_year]`.
3. For M-check, read `reference/m_check.md`; for U-check, read
   `reference/u_check.md`. Never branch methodology by archetype.
4. Write `m_check.json` or `u_check.json` and run the corresponding
   `scripts/validate_*_schema.py` validator.
5. Treat missing keys, sparse evidence, and unknown founding year according to
   the fail/warn rules; never manufacture evidence.

Read `reference/workflow.md` for placeholder definitions, commands, thresholds,
schemas, edge cases, and examples. Query examples are under `examples/`.

## Requirements

Python 3.10+, `tavily-python`, and `TAVILY_API_KEY` for retrieval.
