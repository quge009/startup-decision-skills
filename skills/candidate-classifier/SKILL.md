---
name: candidate-classifier
description: "Generate candidate card 5-dim summary + archetype classification + founding year from a free-text business proposal description. Implements Step 1a (candidate card generation, LLM-based) + Step 1b (archetype classification, LLM-based) of the business-predictive-model v1.5 pipeline. Trigger on: 'classify candidate proposal', 'generate candidate card', 'archetype this proposal', 'evaluate candidate archetype'."
---

# Candidate Classifier (v1.5)

You generate a structured candidate card (5-dim) plus an archetype
classification + a founding year from a free-text business proposal
description. This is Step 1a + Step 1b of the business-predictive-model
v1.5 pipeline.

## v1.5 changes (vs v1.4)

1. **`Founding year` is a REQUIRED metadata field on the candidate card.**
   Extract from free-text; if unrecoverable, emit sentinel `0000` and
   downstream M-check / U-check will halt with "un-evaluable founding
   year".
2. **Archetype is aux output, not verdict-gating.** Classifier still
   emits `primary` (one of 7 archetypes or `out-of-scope`), but downstream
   pipeline runs regardless. No special "OOS short-circuit" instruction
   needed; the orchestrator handles all candidates uniformly.
3. **Methodology reference moved to markdown.** v1.4 cited HTML spec
   files (archetype_taxonomy.html, vendor_synthesis.html); v1.5 uses
   `reference/archetype_taxonomy.md` and `reference/candidate_card_template.md`.
   The HTML files no longer exist.

## Input

A free-text business proposal description. The proposal can come as:
- A file path (e.g. `examples/example_d2_proposal.txt`).
- An inline string block in the message.

You also receive a `working_dir` parameter — the directory where outputs
will be written. If unspecified, default to `./working/<candidate_slug>/`
relative to current working directory, where `<candidate_slug>` is
derived by you from the proposal (kebab-case, max 40 chars).

## Output

Two files written atomically to `<working_dir>`:
- `candidate_card.md` — 5-dim candidate card per
  `reference/candidate_card_template.md` schema (includes REQUIRED
  `Founding year` field)
- `archetype.json` — archetype metadata

Plus a short markdown summary printed to user (primary archetype +
confidence + cn_flag + 1-2 sentence reasoning).

## Workflow

### Step 1 — Read free-text proposal

Either read the file at the given path, or use the inline text block.
Preserve the original text verbatim — it becomes the `Original proposal
description` field in the candidate card and is retained as input for
downstream M-check + U-check.

### Step 2 — Extract founding year

Scan the free-text for the candidate's founding year. Look for:
- Explicit phrasing: "founded in 2017", "Founded: 2017-01-01",
  "established 2017", "since 2017"
- Crunchbase-style metadata: "Founded 2017-01-01", "Founded date: 2017"
- Indirect signals: "5 years ago" combined with a known reference date in
  the text → derive year; first-funding-round year is a fallback only

If multiple plausible years appear, prefer the earliest formal founding
year over a "incorporated" year or "rebrand" year.

If no founding year can be recovered, emit sentinel `0000`. The pipeline
will halt at M-check with a clear error. Do NOT guess a year — guessing
breaks the time-bounded retrieval anti-leakage barrier.

### Step 3 — Generate candidate card 5-dim

Distill the free-text into a 5-dim card following the template at
`reference/candidate_card_template.md`. The card has 5 dimensions (each
~80-150 words):

1. **Revenue model** — primary stream + secondary streams + pricing
   structure + proposal claim + plausibility flag.
2. **Customer segmentation** — target segments + estimated mix + disclosed
   anchor / pilot customers + proposal claim.
3. **Cost structure** — cost model (CapEx own GPU vs OpEx-rented vs
   hybrid) + primary cost driver + estimated GPU spend % of revenue (if
   proposal gives unit economics).
4. **Differentiation / moat** — claimed moat hypothesis (per Helmer 7
   Powers: Network Economies / Scale / Cornered Resource / Process Power /
   Brand / Switching Costs / Counter-positioning, or "none-claimed") +
   plausibility flag per claim.
5. **Strategic vulnerabilities** — list candidate-inherent risks across
   the common categories (GPU-supply dependency / model staleness /
   channel-disintermediation / customer concentration / regulatory
   exposure / cash burn unit-economics / market timing). Always list at
   least 1 risk — this dim cannot be empty.

**Card metadata** (header fields, BEFORE the 5 dims):
- `**Slug**`: kebab-case candidate name
- `**Public/private**`: usually `private` for proposal-stage
- `**Founding year**`: from Step 2 (REQUIRED — use `0000` sentinel if
  unrecoverable)
- `**Card last updated**`: today's date YYYY-MM-DD
- `**Card author**`: `auto-generated (via candidate-classifier skill)`
- `**Original proposal description**`: verbatim free-text
- `**Closest existing vendor analogues**`: select 1-3 most-similar known
  vendors, or "novel candidate, no direct analogue" if truly novel

The candidate card MUST NOT contain an `Archetype` field — archetype is
separate metadata in `archetype.json`.

### Step 4 — Classify archetype

Read the 7 archetype definitions + classification guidance from
`reference/archetype_taxonomy.md`. Synthesize the candidate's free-text +
5-dim card against the archetype definitions to produce archetype
metadata.

**Mandatory: walk through the 4 boundary disambiguations** from
`archetype_taxonomy.md §"Boundary Disambiguations"`:

1. Enterprise/Rental vs OSS-on-rented-GPU (own infra provider vs rental
   consumer)
2. Hardware-vertical vs software-only (own chip design vs target
   hardware)
3. CN-private vs Frontier-model-owner + cn_flag (sovereign / state-backed
   vs not)
4. Distribution vs Hyperscaler-bundle (independent platform vs bundled in
   hyperscaler)

For every candidate (not only borderline), explicitly check each of the 4
boundaries and either confirm "not triggered" or apply the disambiguation
rule. Cite the boundary check trace in your `reasoning` field — see Step
4.1 below.

**Out-of-scope outcome**: if the candidate's business model doesn't
match ANY of the 7 declared archetypes (consumer mobile app, edtech
content publisher, traditional industry SaaS, AI-feature applications
where the candidate is not AI infra), output `primary = "out-of-scope"`.

**v1.5 difference**: in v1.4 `out-of-scope` triggered a pipeline short-
circuit; in v1.5 the downstream pipeline runs anyway (M-check + U-check
still execute). The classifier behavior is unchanged — just emit
`out-of-scope` as before; the orchestrator handles it without
short-circuit.

Output archetype JSON:

```json
{
  "primary":    "<archetype label or 'out-of-scope'>",
  "secondary":  "<archetype label, optional — only if borderline cross-archetype; null when primary='out-of-scope'>",
  "confidence": "robust" | "borderline",
  "cn_flag":    true | false,
  "reasoning":  "<50-250 chars; cites 5-dim + free-text + archetype definition; includes boundary disambiguation trace per Step 4.1>"
}
```

**Confidence heuristic**:
- **robust**: 5-dim features clearly point to a single archetype + free-
  text consistent with 5-dim + high archetype-definition match; OR
  candidate is clearly outside the AI-infra taxonomy with no marginal
  fit.
- **borderline**: 2+ archetypes match partial features / candidate spans
  multiple business types / free-text contradicts 5-dim / 5-dim has
  significant gaps / candidate sits on in-scope ↔ out-of-scope edge.

#### Step 4.1 — Boundary disambiguation trace (mandatory in reasoning)

The `reasoning` field MUST include an explicit boundary disambiguation
trace covering all 4 pairs:

```
Boundary check (per reference/archetype_taxonomy.md):
  - Enterprise/Rental vs OSS-on-rented-GPU: <not triggered | triggered → primary X not Y> — <one-line rationale citing own infra CapEx vs rented>
  - Hardware-vertical vs software-only: <…> — <own chip design vs target hardware>
  - CN-private vs Frontier-model-owner+cn_flag: <…> — <sovereign / state-backed presence>
  - Distribution vs Hyperscaler-bundle: <…> — <independent platform vs bundled inside hyperscaler>
```

This trace is mandatory for every candidate, not just borderline ones —
it forces walking the rules instead of surface-pattern-matching to vendor
analogues from training memory.

### Step 5 — Validate outputs

Run validation scripts before writing:

```bash
python3 ~/.claude/skills/candidate-classifier/scripts/validate_card.py \
    --card-text "$(cat candidate_card.md)"
python3 ~/.claude/skills/candidate-classifier/scripts/validate_archetype.py \
    --json-text "$(cat archetype.json)"
```

Validators check:
- Card: 5 dims present + non-empty Strategic vulnerabilities + REQUIRED
  metadata fields including **Founding year (4-digit, 1900-2099)** + no
  Archetype field.
- Archetype: schema match (primary / secondary / confidence / cn_flag /
  reasoning all in canonical form).

If validation fails, fix the issue and re-validate. Do not write outputs
until both validators return success.

### Step 6 — Write outputs

```bash
python3 ~/.claude/skills/candidate-classifier/scripts/write_outputs.py \
    --working-dir <working_dir> \
    --card-text "$(cat candidate_card.md)" \
    --archetype-json "$(cat archetype.json)"
```

The script writes both files atomically (write-then-rename) and prints
the final paths.

### Step 7 — Print summary

```
## Candidate classification result

**Slug**: <candidate-slug>
**Founding year**: <YYYY>
**Primary archetype** (aux): <archetype label>
**Secondary archetype**: <if borderline, else "—">
**Confidence**: <robust | borderline>
**CN flag**: <true | false>
**Reasoning**: <text including boundary disambiguation trace>

**Outputs written**:
- <working_dir>/candidate_card.md
- <working_dir>/archetype.json
```

## Edge cases

- **Free-text < 50 chars**: each dim's plausibility flag = low; mark each
  dim caveat with specific missing items; archetype confidence =
  borderline. Do not block — produce best-effort output with low
  confidence flags.
- **Cross-archetype proposal**: list all relevant claims in 5-dim
  faithfully; output `primary` = dominant feature archetype, `secondary`
  = next-strongest archetype, `confidence` = borderline. In v1.5 this
  does NOT trigger a re-run of M/U-check — archetype is aux output only,
  cross-archetype is informational.
- **No close vendor analogue**: `Closest existing vendor analogues`
  field = "novel candidate, no direct analogue".
- **Vulnerabilities dim sparse**: must have at least 1 risk. Infer from
  archetype-typical failure modes if free-text doesn't supply.
- **Founding year unrecoverable**: emit sentinel `0000` in candidate
  card. validate_card.py will accept this (it only checks 4-digit format,
  not realism); downstream M-check / U-check halt with clear error and
  the candidate gets an "un-evaluable" FAIL verdict.

## Reference docs

All reference docs are bundled with this skill in `reference/`:

- `reference/archetype_taxonomy.md` — 7 archetype definitions + 4
  boundary disambiguations + out-of-scope rule + classifier guidance.
  **Primary methodology source for Step 4.**
- `reference/candidate_card_template.md` — 5-dim candidate card template
  + metadata field schema including **Founding year**.

v1.5 is self-contained — no external HTML, no `make sync-spec-to-skills`,
no host `docs/` dependency.

## Examples

See `examples/example_d2_proposal.txt` for a sample free-text input +
`examples/example_d2_card.md` + `examples/example_d2_archetype.json` for
expected outputs (D2 = AI Agent Orchestration Marketplace, archetype =
Distribution).
