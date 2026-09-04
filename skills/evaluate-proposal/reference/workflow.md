# Evaluate proposal: end-to-end workflow

This workflow coordinates four independently installable skills:
`candidate-profiler`, `market-check`, `moat-check`, and `evaluate-proposal`.
It preserves every intermediate artifact so the final assessment is auditable.

## Inputs and outputs

Input: a free-text startup proposal and a working directory.

Outputs:

- `free_text.txt`: original proposal
- `candidate_card.md`: five-dimension proposal profile and founding year
- `placeholders.json`: query variables derived from the proposal profile
- `market_queries.json` and `moat_queries.json`: independent query batches
- `tavily_m_results.json` and `tavily_u_results.json`: time-bounded evidence
- `m_check.json` and `u_check.json`: independently interpreted verdicts
- `aggregate.json`: deterministic M/U score and final verdict
- `reasoning.json`: evidence-linked explanation

## Workflow

### 1. Preserve and profile the proposal

Save the input verbatim as `free_text.txt`. Invoke `candidate-profiler` to
create and validate `candidate_card.md`. The card must include a four-digit
`Founding year`; `0000` means it could not be recovered from supplied material.

### 2. Prepare independent checks

Derive `placeholders.json` from the proposal and candidate card. Run the query
builder in each check separately:

```bash
python3 ~/.claude/skills/market-check/scripts/build_queries.py \
  --working-dir <working_dir>
python3 ~/.claude/skills/moat-check/scripts/build_queries.py \
  --working-dir <working_dir>
```

Review and resolve any placeholder warnings before retrieval.

### 3. Run the market check

Use `market-check` to retrieve evidence published within
`[founding_year - 3 years, founding_year]`, interpret market headroom and
competitive forces, and validate `m_check.json`.

```bash
python3 ~/.claude/skills/market-check/scripts/run_tavily.py \
  --working-dir <working_dir> --founding-year <YYYY>
```

### 4. Run the moat check

Use `moat-check` over the same time boundary to assess VRIO strength and
erosion risk, then validate `u_check.json`.

```bash
python3 ~/.claude/skills/moat-check/scripts/run_tavily.py \
  --working-dir <working_dir> --founding-year <YYYY>
```

### 5. Aggregate

```bash
python3 ~/.claude/skills/evaluate-proposal/scripts/aggregate.py \
  --working-dir <working_dir>
```

The deterministic rule is:

```text
PASS=1.0, WARN=0.5, FAIL=0.0
score = 0.5 * M + 0.5 * U
PASS if score >= 0.50; WARN if score >= 0.25; otherwise FAIL
```

No company category or archetype changes query selection, weights, thresholds,
or the verdict.

### 6. Explain and validate

Compose `reasoning.json` from `free_text.txt`, `candidate_card.md`, both check
files, and `aggregate.json`. It must include the final verdict, M/U evidence,
score explanation, dominant driver, actionable rationale, founding year, and
retrieval window. Validate it with:

```bash
python3 ~/.claude/skills/evaluate-proposal/scripts/validate_reasoning_schema.py \
  --json-file <working_dir>/reasoning.json
```

## Failure behavior

- Missing Tavily credentials: halt retrieval and report the missing credential.
- Founding year `0000`: halt time-bounded retrieval and report the assessment as
  unevaluable; do not silently search an unrestricted period.
- Missing evidence: follow each check's documented WARN/FAIL behavior; do not
  invent values.
- Schema failure: stop at the failing artifact and correct or regenerate it.
- Existing artifacts: review their provenance before reusing them.

## References

- `candidate-profiler/reference/candidate_card_template.md`
- `market-check/reference/m_check.md`
- `moat-check/reference/u_check.md`
- `evaluate-proposal/scripts/aggregate.py`
