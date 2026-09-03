---
name: tavily-query-builder
description: "Build structured M-check and U-check search queries from a startup candidate card using uniform, archetype-agnostic templates. Use when preparing evidence retrieval for the business-proposal evaluation pipeline."
---

# Tavily Query Builder (v1.5)

You instantiate per-candidate tavily search queries by combining uniform
query templates (archetype-agnostic) with placeholder values extracted
from the candidate's card. This is the deterministic templating step
that feeds M-check and U-check — actual Tavily execution is handled by
the `check-interpreter` skill's `run_tavily.py` (which also applies
time-bounded retrieval).

## v1.5 changes (vs v1.4)

1. **Archetype-agnostic templates.** v1.4 had per-archetype query
   templates (one set per Distribution / Frontier-MO / OSS-on-rented-GPU
   / Hyperscaler-bundle / etc.). v1.5 collapses to **one uniform set**
   of M-check queries (5) and one uniform set of U-check queries (4),
   applied to every candidate regardless of archetype.
2. **archetype.json is audit-only.** The skill still reads `archetype.json`
   to record `primary` / `secondary` in the output for traceability, but
   does NOT use it to select templates.
3. **`cloud_platform` placeholder dropped.** It was Hyperscaler-bundle-
   specific; not needed in uniform templates.
4. **Time-bounded retrieval is downstream.** Query templates remain
   time-neutral; the `[founding_year - 3y, founding_year]` window is
   applied at Tavily execution by `run_tavily.py` via `start_date` /
   `end_date` params, not embedded in the query text.

## Input

Files in `<working_dir>` (from candidate-classifier output):

- `candidate_card.md` — 5-dim card
- `archetype.json` — archetype metadata (used for audit-trail only in
  v1.5)

Plus optional `free_text.txt` — original free-text proposal, used for
placeholder values not directly recoverable from 5-dim alone.

## Output

Two files written to `<working_dir>`:

- `placeholders.json` — extracted placeholder values (canonical names +
  values)
- `queries.json` — instantiated M-check + U-check query lists, ready for
  Tavily execution

## Workflow

### Step 1 — Extract placeholder values

Read `candidate_card.md` + `archetype.json` and produce
`placeholders.json` with 7 canonical placeholders. Most fields are filled
via LLM judgment from the candidate card; some (`candidate`,
`geographic_scope`) are derived directly from metadata.

Canonical placeholders (v1.5):

| Placeholder | Meaning | Where to extract from |
|---|---|---|
| `candidate` | Candidate name / shorthand label | Candidate card `**Slug**` metadata |
| `target_market` | High-level market scope (TAM unit) | Candidate card customer dim + free-text — "AI inference API market", "enterprise LLM", "GPU NeoCloud", etc. |
| `sub_segment` | Sub-market scope (SAM unit) | Candidate card customer + cost dim + free-text — "agent runtime", "long context inference", "speech API", etc. |
| `incumbent` | Known incumbent vendor name(s), comma list | Candidate card "Closest existing vendor analogues" + likely incumbents in target_market |
| `geographic_scope` | global / US / EU / CN / sovereign-bound | Candidate card customer dim geographic skew + `archetype.json` `cn_flag` |
| `specialty_capability` | Differentiation capability | Candidate card moat dim — "long context", "agentic", "wafer-scale chip", "domain-tuned", etc. |
| `moat_claim` | Moat claim type (per Helmer 7 Powers) | Candidate card moat dim primary moat hypothesis — "Network Economies", "Cornered Resource", etc. |

**Note**: v1.4 had a `cloud_platform` placeholder (Hyperscaler-bundle
only). v1.5 drops it.

Output format `placeholders.json`:

```json
{
  "candidate": "d2-agent-orchestration-marketplace",
  "target_market": "AI agent platform market",
  "sub_segment": "agent runtime",
  "incumbent": "OpenAI GPT Store, Anthropic Claude Skills, HuggingFace, OpenRouter",
  "geographic_scope": "global",
  "specialty_capability": "multi-vendor backend agent orchestration",
  "moat_claim": "Network Economies"
}
```

Use `null` for placeholders that can't be recovered. `build_queries.py`
will leave a `<null:name>` token in the query string and emit a warning.

### Step 2 — Run build_queries.py

```bash
python3 ~/.claude/skills/tavily-query-builder/scripts/build_queries.py \
    --working-dir <working_dir> \
    --placeholders-file <working_dir>/placeholders.json
```

The script reads `placeholders.json`, applies the uniform M-check + U-check
templates from `scripts/templates.py`, substitutes `{{name}}` placeholders,
and writes `<working_dir>/queries.json`.

### Step 3 — Confirm output

```
## Tavily query build result (v1.5)

**Candidate**: <slug>
**Archetype** (audit-trail only): <archetype>
**M-check queries**: 5 (uniform)
**U-check queries**: 4 (uniform)
**Warnings**: <count, if any> (unresolved placeholders)

**Output written**: <working_dir>/queries.json
```

## Output schema (queries.json)

```json
{
  "candidate": "<slug>",
  "archetype": {
    "primary":   "<archetype label>",
    "secondary": "<archetype label or null>"
  },
  "m_check": {
    "queries": ["<query 1>", "<query 2>", "<query 3>", "<query 4>", "<query 5>"],
    "extract_targets": [
      "Gartner / IDC / McKinsey / Sacra TAM forecast for target_market",
      "Peer-vendor ARR + valuation in target_market (for back-inference TAM)",
      "Incumbent occupancy of SAM (locked_pct)",
      "Porter Five Forces signal (rivalry / substitutes / new_entrants)"
    ],
    "note": "Time-bounded retrieval window is applied at Tavily execution..."
  },
  "u_check": {
    "queries": ["<query 1>", "<query 2>", "<query 3>", "<query 4>"],
    "focus_notes": [
      "VRIO: Valuable — ...",
      "VRIO: Rare — ...",
      "VRIO: Inimitable — ...",
      "VRIO: Organized — ...",
      "Erosion risks: OSS substitutes, low-cost alternatives, ..."
    ],
    "note": "U-check Tavily is lighter than M-check ..."
  },
  "placeholders": { ... },
  "warnings": ["<unresolved-placeholder warning string>", "..."]
}
```

## Edge cases

- **Placeholder value unknown / not applicable**: set to `null` in
  `placeholders.json`. `build_queries.py` will emit queries with
  `<null:name>` tokens and add a warning. Resolve before running Tavily
  (either fill the placeholder or skip that specific query manually).
- **Multi-archetype candidate (`secondary` set in archetype.json)**:
  in v1.5 this does NOT affect query building — templates are uniform.
  The `secondary` is recorded in `queries.json` for audit but no
  duplicate query set is produced.

## Reference

Templates live in `scripts/templates.py` (uniform M-check + U-check
templates). To change templates, edit that file directly.

v1.5 is self-contained — no external HTML dependency, no `make
sync-spec-to-skills`. Templates are the canonical source.

## Examples

See `examples/example_d2_placeholders.json` +
`examples/example_d2_queries.json` for sample input/output (D2 = AI Agent
Orchestration Marketplace).
