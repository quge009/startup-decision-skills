# Moat-check workflow

## Inputs

- `candidate_card.md`
- `placeholders.json`
- A valid founding year or an explicit retrieval window

## Execution

1. Run `scripts/build_queries.py --working-dir <dir>` to produce
   `moat_queries.json` containing only the `u_check` query section.
2. Run `scripts/run_tavily.py --working-dir <dir> --founding-year <year>`.
3. Interpret the bounded evidence using `u_check.md` and write `u_check.json`.
4. Run `scripts/validate_u_schema.py --json-file <dir>/u_check.json`.

## Output contract

The accepted output is `u_check.json`. It records claimed moats, the four VRIO
dimensions, erosion risks, the retrieval window, source URLs, and a
PASS/WARN/FAIL verdict. It does not estimate market headroom or aggregate the
final proposal verdict.

Company-category-specific required moat types are prohibited. Missing
founding-year context, unresolved required query placeholders, or fabricated
evidence are hard failures; sparse evidence follows the WARN/FAIL rules in
`u_check.md`.
