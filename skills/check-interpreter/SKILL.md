---
name: check-interpreter
description: "Run an archetype-agnostic M-check or U-check on a startup candidate and emit validated verdict JSON from time-bounded Tavily evidence. Use for market-headroom or moat-strength checks in the business-proposal evaluation pipeline."
---

# Check Interpreter (v1.5)

You run M-check or U-check on a candidate and emit verdict JSON
conforming to the canonical schema. Both checks are LLM-based
interpretation of Tavily search results, paired with the candidate card
+ free-text.

## v1.5 changes (vs v1.4)

1. **H-check dropped entirely.** v1.4 had `--check h`; v1.5 supports
   only `--check m` and `--check u`. H-check methodology + variants +
   schema all gone.
2. **M/U-check are archetype-agnostic.** No per-archetype prompts,
   thresholds, query templates, weight tables, or "required moat type"
   lookup. Uniform across all candidates including out-of-scope ones.
3. **Tavily retrieval is time-bounded.** Pass
   `--founding-year YYYY` to `run_tavily.py`. Tavily searches restricted
   to `[founding_year - 3y, founding_year]` as anti-leakage barrier.
4. **No archetype-keyed methodology lookup.** The check just reads the
   candidate's card, free-text, archetype.json (for `founding_year`
   only — NOT to branch logic), and tavily results, then applies the
   uniform M-check or U-check rules in `reference/m_check.md` and
   `reference/u_check.md`.

## Input

Files in `<working_dir>` (from earlier pipeline steps):

- `candidate_card.md` — 5-dim card with `Founding year` (from
  candidate-classifier)
- `archetype.json` — archetype metadata (read for `founding_year` if not
  in candidate card; **NOT used to branch logic**)
- `free_text.txt` — original proposal
- `queries.json` — Tavily queries (from tavily-query-builder)

Plus a `--check` parameter selecting which check: `m` or `u`. (To run
both, invoke this skill twice sequentially.)

## Output

A file `<check>_check.json` written to `<working_dir>`:

- `m_check.json` — M-check verdict (TAM/SAM/SOM + Porter; per
  `reference/m_check.md`)
- `u_check.json` — U-check verdict (VRIO + claimed moats + erosion risks;
  per `reference/u_check.md`)

Plus a short markdown summary printed to user (verdict + key reasoning).

## Workflow

### Step 1 — Read inputs + resolve founding_year

Parse `<working_dir>/candidate_card.md` to extract `Founding year`
(match `**Founding year**: <YYYY>`). If sentinel `0000` (un-recovered) or
missing, halt with error: "founding_year required for time-bounded
retrieval".

Read `free_text.txt`, `archetype.json` (metadata only),
`queries.json`.

### Step 2 — Execute Tavily (time-bounded)

```bash
python3 ~/.claude/skills/check-interpreter/scripts/run_tavily.py \
    --working-dir <working_dir> \
    --check <m|u> \
    --founding-year <YYYY>
```

The script auto-computes window `[founding_year - 3y, founding_year]`
and passes `start_date` / `end_date` to Tavily so results are restricted
to publications in that window. Writes
`<working_dir>/tavily_<check>_results.json` with snippets + URLs +
published_date per result + the resolved retrieval window.

### Step 3 — Interpret (uniform M-check or U-check methodology)

Read the methodology spec from the appropriate reference doc:

- For `--check m`: `reference/m_check.md`
- For `--check u`: `reference/u_check.md`

Apply the methodology end-to-end to produce the verdict JSON.

#### M-check (per `reference/m_check.md`)

1. From Tavily results + candidate card + free-text, extract **archetype-
   wide TAM** via dual-evidence triangulation (peer-vendor ARR
   back-inference + analyst forecast). Reject candidate-framing TAM.
2. Derive `pct_of_tam` (candidate filter coefficient) from card customer
   dim + free-text — geographic / regulatory / customer-segment / tech-
   segment filters compose into a single coefficient 0.0–1.0.
3. Extract `locked_pct` (incumbent share of SAM) + Porter Five Forces
   (rivalry / substitutes / new_entrants) from Tavily.
4. Compute `SAM = TAM × pct_of_tam`,
   `SOM_headroom = SAM × (1 − locked_pct) × porter_discount`.
5. Verdict thresholds:
   - **✅ PASS**: SOM_headroom ≥ $1B AND porter_discount ≥ 0.7
   - **⚠ WARN**: $100M ≤ SOM_headroom < $1B OR porter_discount = 0.5
   - **🔴 FAIL**: SOM_headroom < $100M OR porter_discount = 0.3
6. Output `m_check.json` per the schema in `reference/m_check.md`.
   Includes `search_metadata.founding_year` + `retrieval_window` fields.

**Archetype-agnostic note**: do NOT condition the methodology on
archetype — same prompts, same thresholds, same query interpretation
across all candidates. v1.4 had per-archetype sub-segment baselines +
per-archetype query templates — these are explicitly dropped in v1.5.

**No trajectory check**: v1.4 had an "archetype-wide TAM trajectory check
vs 6mo prior baseline archive". v1.5 drops this — the time-bounded
retrieval window already captures decision-time market state; no
trajectory comparison needed.

#### U-check (per `reference/u_check.md`)

1. Extract candidate's **claimed moats** from card moat dim + free-text.
   Classify each into Helmer 7 Powers (Network Economies / Scale /
   Cornered Resource / Process Power / Brand / Switching Costs /
   Counter-positioning). Pick the dominant.
2. Run **VRIO 4-dim audit** on the dominant moat using card + free-text
   + Tavily evidence (especially for R = rare and I = inimitable, which
   depend on incumbent landscape):
   - Valuable: ✓ / partial / ✗ (1.0 / 0.5 / 0.0)
   - Rare: ✓ / partial / ✗
   - Inimitable: ✓ / partial / ✗
   - Organized to capture: ✓ / partial / ✗
   - `vrio_score` = sum of 4 = range 0.0–4.0
3. Extract `incumbent_moat_depth` (`defensible` / `eroding` /
   `commoditized`) + `erosion_risks[]` from Tavily.
4. Verdict thresholds:
   - **✅ PASS**: vrio_score ≥ 3.5 AND incumbent_moat_depth = defensible
   - **⚠ WARN**: 2.0 ≤ vrio_score < 3.5 OR incumbent_moat_depth = eroding
   - **🔴 FAIL**: vrio_score < 2.0 OR candidate makes no moat claim OR
     incumbent_moat_depth = commoditized
5. Output `u_check.json` per the schema in `reference/u_check.md`.

**No "required moat type per archetype"**: v1.4 mapped each archetype to
a required moat type (Distribution → Network Economies, Frontier-MO →
Cornered Resource, etc.). v1.5 drops this — VRIO audit alone is the
moat-strength filter. The `dominant_moat_required` field is removed from
the JSON schema.

### Step 4 — Validate output

Run schema validation:

```bash
python3 ~/.claude/skills/check-interpreter/scripts/validate_<m|u>_schema.py \
    --json-file <working_dir>/<check>_check.json
```

Address any FAIL errors before proceeding.

### Step 5 — Print summary

```
## <M|U>-check result (v1.5)

**Candidate**: <slug>
**Founding year**: <YYYY> (Tavily window: <start> to <end>)
**Verdict**: <emoji> <verdict_text>
[Check-specific: M som_headroom_b / U vrio_score + incumbent_moat_depth]
**Reasoning** (50-200 chars): <text>

**Output**: <working_dir>/<check>_check.json
```

## Tavily key

`scripts/run_tavily.py` reads the API key from the `TAVILY_API_KEY`
environment variable. Never store a key in the skill directory or generated
artifacts. The helper waits 1.5 seconds between calls by default and requests
four results per query.

## Edge cases

- **`founding_year` is sentinel 0000 (un-recovered) or missing**: halt
  with clear error. Time-bounded retrieval cannot proceed; downstream
  aggregate treats this as un-evaluable candidate (FAIL).
- **Tavily key not available**: `run_tavily.py` exits with helpful
  error. Skill cannot proceed for M-check / U-check.
- **Tavily returns sparse / no relevant results in window**: M-check /
  U-check verdict capped at ⚠ WARN (data confidence low). Reasoning
  notes "insufficient evidence in retrieval window".
- **Cross-archetype candidate (`secondary` set)**: in v1.5 this does NOT
  cause a re-run. M/U-check is archetype-agnostic — runs once. The
  `secondary` is informational only and noted in reasoning.
- **Candidate has no claimed moat** (U-check only): `claimed_moats =
  []`, `vrio_breakdown = null` or all ✗, verdict = FAIL.

## Reference docs

Bundled in this skill's `reference/`:

- `reference/m_check.md` — M-check methodology + JSON schema. **Primary
  source for `--check m` workflow.**
- `reference/u_check.md` — U-check methodology + JSON schema. **Primary
  source for `--check u` workflow.**

v1.5 is self-contained — no external HTML, no host docs/ dependency.
H-check has no reference doc (dropped).
