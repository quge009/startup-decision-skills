# Market-check workflow

## Inputs

- `candidate_card.md`
- `placeholders.json`
- A valid founding year or an explicit retrieval window

## Execution

1. Run `scripts/build_queries.py --working-dir <dir>` to produce
   `market_queries.json` containing only the `m_check` query section.
2. Run `scripts/run_tavily.py --working-dir <dir> --founding-year <year>`.
3. Interpret the bounded evidence using `m_check.md` and write `m_check.json`.
4. Run `scripts/validate_m_schema.py --json-file <dir>/m_check.json`.

## Output contract

The accepted output is `m_check.json`. It records TAM, SAM, SOM headroom,
Porter-force evidence, the retrieval window, source URLs, and a PASS/WARN/FAIL
verdict. It does not perform moat assessment or final verdict aggregation.

Missing founding-year context, unresolved required query placeholders, or
fabricated evidence are hard failures. Sparse in-window evidence follows the
WARN/FAIL rules in `m_check.md`.
