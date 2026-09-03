---
name: evaluate-proposal
description: Evaluate a business proposal through candidate classification, time-bounded market and uniqueness research, evidence interpretation, scoring, and structured reasoning. Use for an end-to-end evidence-grounded startup assessment.
---

# Evaluate Proposal — End-to-End Pipeline Orchestrator (v1.5a)

You orchestrate the v1.5a business-predictive-model predictive model
pipeline. Given a free-text business proposal description, you run the
six steps and write per-candidate structured outputs to the working
directory. No HTML storage is produced — cohort-level rollup (if any) is
the calling driver's responsibility (reads `aggregate.json` +
`reasoning.json` per working dir).

**v1.5a vs v1.5**: PASS_THRESHOLD lowered 0.75 → 0.50 in `aggregate.py`
using a frozen, documented calibration threshold. Score formula,
M/U-check, archetype handling, retrieval window — all unchanged.

This skill internally invokes three sibling skills (`candidate-classifier`,
`tavily-query-builder`, `check-interpreter`) and finishes with
deterministic aggregation (`aggregate.py`) + LLM reasoning composition.

## v1.5 architectural changes (vs v1.4)

These changes are encoded throughout the pipeline below. See
`pipeline_benchmark/_iterations/1/summary.md §6.2 Decision A` for the
motivation.

1. **Archetype is auxiliary output, not verdict-gating.** Archetype is
   produced (Step 1b) but does NOT short-circuit the pipeline. Every
   candidate runs through M-check + U-check, regardless of archetype
   (including `out-of-scope`).
2. **H-check is dropped.** v1.4 had H-check at Step 3; v1.5 has no
   H-check at all. Aggregate uses M+U only.
3. **M/U-check are archetype-agnostic.** No per-archetype prompts,
   thresholds, query templates, or weight tables. Uniform across all
   candidates.
4. **M/U-check Tavily retrieval is time-bounded.** All Tavily searches
   restricted to `[founding_year - 3y, founding_year]` as an anti-leakage
   barrier. Requires `founding_year` to be present in `candidate_card.md`.

## Input

A free-text business proposal description. Either:
- Inline string in the message
- File path (e.g. `examples/example_d2_proposal.txt`)

Plus optional:
- `--working-dir <path>` — defaults to `./working/<candidate_slug>/`
- `--skip-tavily` — for offline / dev runs, skip M/U-check tavily
  execution and emit data-thin verdicts

## Output

A complete pipeline run producing files in `<working_dir>`:
- `free_text.txt` — preserved original proposal
- `candidate_card.md` — 5-dim card with `Founding year` (Step 1a)
- `archetype.json` — archetype metadata (Step 1b; auxiliary output)
- `placeholders.json` + `queries.json` — tavily query batch
- `tavily_m_results.json` + `tavily_u_results.json` — tavily search
  results (time-bounded retrieval window recorded in metadata)
- `m_check.json`, `u_check.json` — per-dim verdicts (no `h_check.json`)
- `aggregate.json` — score + verdict_path + dominant_driver
- `reasoning.json` — final reasoning per v1.5 schema (validated by
  `validate_reasoning_schema.py`)

Plus a markdown summary printed to user.

## Workflow

### Step 0 — Setup

Save free-text proposal to `<working_dir>/free_text.txt`. Create working
dir if missing.

### Step 1a + 1b — Candidate card + archetype (invoke candidate-classifier skill)

Invoke the `candidate-classifier` skill with the free-text input + working
dir. It produces `candidate_card.md` (5-dim) and `archetype.json`. Validate
both before proceeding.

**Critical for v1.5**: `candidate_card.md` MUST include a `Founding year`
field with a 4-digit year (1900-2099). The candidate-classifier extracts
this from the free-text. If unrecoverable, the classifier emits `Founding
year: 0000` (sentinel) — see Edge Cases below.

**No OOS short-circuit**: in v1.4, if `archetype.json.primary ==
"out-of-scope"`, the pipeline skipped Steps 2-7. **In v1.5, the pipeline
proceeds regardless**. Out-of-scope candidates still go through M-check +
U-check + aggregate + reasoning. Archetype is preserved in
`archetype.json` purely as aux output for diagnostics.

### Step 2 — Build tavily queries (invoke tavily-query-builder skill)

Invoke the `tavily-query-builder` skill with `<working_dir>` to produce
`placeholders.json` + `queries.json`. Queries are uniform across all
archetypes (per v1.5 #3 archetype-agnostic). Verify there are no
unresolved placeholders (warn if any).

### Step 3 — M-check (invoke check-interpreter skill, --check m)

Read `Founding year` from `<working_dir>/candidate_card.md` (parse line
matching `**Founding year**: <YYYY>`).

If `--skip-tavily`: emit data-thin `m_check.json` (verdict=WARN,
reasoning="tavily skipped") and proceed.

Otherwise:

```bash
python3 ~/.claude/skills/check-interpreter/scripts/run_tavily.py \
    --working-dir <working_dir> --check m \
    --founding-year <YYYY>
```

This writes `tavily_m_results.json` with results restricted to
publications in `[founding_year - 3y, founding_year]`.

Then invoke `check-interpreter --check m --working-dir <working_dir>`.
The skill reads candidate card + archetype.json (for `founding_year`
backup) + queries.json + tavily_m_results.json and applies the M-check
methodology (see `check-interpreter/reference/m_check.md`). Produces
`m_check.json`. Validate against `m_check.json` schema.

### Step 4 — U-check (invoke check-interpreter skill, --check u)

Same pattern as Step 3 with `--check u`:

```bash
python3 ~/.claude/skills/check-interpreter/scripts/run_tavily.py \
    --working-dir <working_dir> --check u \
    --founding-year <YYYY>
```

Then invoke `check-interpreter --check u --working-dir <working_dir>`.
Produces `u_check.json`. Validate against `u_check.json` schema.

### Step 5 — Aggregate (deterministic, M+U only)

```bash
python3 ~/.claude/skills/evaluate-proposal/scripts/aggregate.py \
    --working-dir <working_dir>
```

Reads `archetype.json` (metadata only) + `m_check.json` + `u_check.json`.
Applies the v1.5 score formula:

```
m_num, u_num = VERDICT_TO_NUM[verdict]    # PASS=1.0, WARN=0.5, FAIL=0.0
score        = 0.5 * m_num + 0.5 * u_num
verdict      = PASS if score >= 0.50 else
               WARN if score >= 0.25 else
               FAIL
```

Writes `aggregate.json` with `final_verdict` + `weighted_score` +
`verdict_path: "score_threshold"` + `score_explanation` +
`dominant_driver`.

There is no conjunction rule, no archetype-keyed weight lookup, no
out-of-scope short-circuit. See `evaluate-proposal/scripts/aggregate.py`
for the canonical implementation.

### Step 6 — Generate reasoning (LLM-based)

Read all outputs (`free_text.txt` + `candidate_card.md` + `archetype.json`
+ `m_check.json` + `u_check.json` + `aggregate.json`) and compose a final
reasoning JSON conforming to v1.5 schema:

```json
{
  "verdict":         "PASS" | "WARN" | "FAIL",
  "verdict_emoji":   "✅" | "⚠" | "🔴",
  "verdict_text":    "<one-line verdict summary>",
  "archetype":       {"primary": ..., "secondary": ..., "cn_flag": ...},
  "mu_breakdown": {
    "m_check":       {"verdict": ..., "som_headroom_b": ..., "key_evidence": ...},
    "u_check":       {"verdict": ..., "vrio_score": ..., "key_evidence": ...}
  },
  "aggregate": {
    "weighted_score":    ...,
    "weights_used":      {"m": 0.5, "u": 0.5},
    "verdict_path":      "score_threshold",
    "score_explanation": "..."
  },
  "dominant_driver":   {"dim": "M" | "U", "verdict": ..., "reason": ...},
  "decision_rationale": "<50-200 chars actionable direction>",
  "metadata": {
    "timestamp":         "<YYYY-MM-DD>",
    "framework_version": "v1.5",
    "founding_year":     <int>,
    "retrieval_window":  ["<YYYY-MM-DD>", "<YYYY-MM-DD>"]
  }
}
```

Decision rationale should be actionable (e.g. "不推荐独立做; 可作 D2 backend
feature" or "推荐 — anchor lock + verifiable VRIO").

Write to `<working_dir>/reasoning.json`. Validate with:

```bash
python3 ~/.claude/skills/evaluate-proposal/scripts/validate_reasoning_schema.py \
    --json-file <working_dir>/reasoning.json
```

### Step 7 — Print summary

```
## Pipeline run complete (v1.5a)

**Candidate**:  <slug>
**Founding year**: <YYYY> (Tavily retrieval window: <start> to <end>)
**Archetype** (aux): <primary> [+ secondary if borderline] [+ CN flag] · confidence <robust|borderline>
**Final verdict**: <emoji> <verdict> (score <weighted_score>)

**M-check**: <emoji> <verdict> (TAM $X-YB · SAM $ZB · SOM headroom $W B)
**U-check**: <emoji> <verdict> (VRIO X.X/4.0 · dominant moat <type>)

**Decision rationale**: <text>

**Outputs in**: <working_dir>
```

Do NOT enter interactive follow-up mode.

## Edge cases

- **`--skip-tavily` flag**: M/U-check emit data-thin verdict
  (verdict=WARN, all fields filled with "tavily skipped" placeholders) —
  useful for dev iteration without burning tavily quota.
- **Tavily key not available**: pipeline halts at Step 3. Surface the
  error + suggest setting `TAVILY_API_KEY` or using `--skip-tavily`.
- **`Founding year` unrecoverable (sentinel 0000)**: M-check and U-check
  halt because time-bounded retrieval cannot be computed. Emit
  `aggregate.json` with `final_verdict: "FAIL"` +
  `verdict_text: "un-evaluable: founding year missing"`. Compose a
  minimal `reasoning.json` recording the halt.
- **Validation failure mid-pipeline**: halt + report which step's output
  failed schema validation. Reviewer should fix the LLM output (or re-run
  that skill) before proceeding.
- **Cross-archetype candidate (`secondary` set in archetype.json)**:
  archetype is aux output in v1.5, so cross-archetype does NOT cause a
  re-run of check-interpreter. M-check + U-check are archetype-agnostic
  and run once. Note the `secondary` in `reasoning.json` for
  documentation.
- **Working dir already has artifacts from prior run**: skip steps whose
  outputs already exist (idempotent re-run). To force fresh run, delete
  working dir first.

## Reference docs

Methodology references are bundled in the two sibling skills below. The v1.5a
workflow is self-contained once all four pipeline skills are installed; it has
no external HTML or host `docs/` dependency.

- `candidate-classifier/reference/archetype_taxonomy.md` — 7 archetype
  definitions + classifier guidance (referenced by Step 1b)
- `candidate-classifier/reference/candidate_card_template.md` — 5-dim
  card template + metadata fields including Founding year
- `check-interpreter/reference/m_check.md` — M-check methodology + JSON
  schema (referenced by Step 3)
- `check-interpreter/reference/u_check.md` — U-check methodology + JSON
  schema (referenced by Step 4)
