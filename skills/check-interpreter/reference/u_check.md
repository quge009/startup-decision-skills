# U-check Framework (v1.5)

## Purpose

Reference doc for the `check-interpreter` skill when invoked with
`--check u`. Defines the **uniqueness / moat** methodology — does the
candidate have defensible competitive advantage strong enough to sustain
captured market value?

**v1.5 status**: U-check is **archetype-agnostic** (per Decision A #3).
All candidates audit their claimed moats via the same VRIO methodology.
No per-archetype "required moat type" — that v1.4 mechanism is dropped.

**v1.5 status**: U-check Tavily retrieval is **time-bounded**
(per Decision A #4). Tavily searches restricted to
`[founding_year - 3y, founding_year]` to model what a decision-time
investor could know about incumbent moat depth in the candidate's
claimed moat category, without leaking outcome.

## Input

U-check consumes:

- `free_text.txt` — original proposal
- `candidate_card.md` — 5-dim distillation (including `founding_year` and
  `moat` dim — candidate's self-reported moat claims)
- `archetype.json` — read for `founding_year` if not in candidate card;
  NOT used to branch prompts or look up required moat type
- `queries.json` — Tavily query list from `tavily-query-builder`
- `tavily_u_results.json` — Tavily search results from
  `check-interpreter/scripts/run_tavily.py` (time-bounded window)

## Methodology overview

U-check audits the candidate's **claimed moat** against the Helmer
7-Powers classification + Barney VRIO 4-dim framework:

### Helmer 7 Powers (moat types — taxonomy only)

A moat falls into one of seven categories. The classification is
informational; v1.5 does NOT prefer one moat type over another based on
archetype.

- **Network Economies** — value grows with users (two-sided marketplace,
  social graph)
- **Scale Economies** — unit cost drops with volume
- **Cornered Resource** — exclusive access to a unique input (patent,
  exclusive data, key talent)
- **Process Power** — proprietary operating know-how compounding over
  time (e.g. Toyota TPS)
- **Brand** — customer preference at price premium
- **Switching Costs** — high cost / friction for customers to leave
- **Counter-positioning** — incumbent can't copy without cannibalizing
  own business

### Barney VRIO (4-dim audit on the candidate's claimed moat)

For the candidate's stated dominant moat, score on four dimensions:

- **V — Valuable**: does it solve a real customer pain / unlock value?
- **R — Rare**: is it scarce in the competitive landscape?
- **I — Inimitable**: can competitors NOT copy it cheaply / quickly?
- **O — Organized to capture value**: is the candidate's business
  organized (team, GTM, operations) to extract value from the moat?

Each dim scored 3-level:

```
✓ (Yes)    = 1.0  fully satisfied, no significant caveat
partial    = 0.5  partially satisfied / notable caveat / not fully robust
✗ (No)     = 0.0  not satisfied / counter-evidence
```

`VRIO_score = V + R + I + O`  (range 0.0–4.0)

## Step-by-step procedure

### Step 1 — Read inputs

Read `free_text.txt`, `candidate_card.md`, `archetype.json`,
`queries.json`. Extract `founding_year` (required for time-bounded
retrieval in Step 3).

### Step 2 — Extract candidate's claimed moats

From `candidate_card.md` moat dim + `free_text.txt`, identify the
candidate's self-reported moat claims. Each claim is classified into one
of the seven Helmer powers above. Pick the **dominant moat** (the one
the candidate most heavily relies on as their structural advantage); the
remaining are secondary.

If the candidate makes no moat claim (`moat = "none-claimed"` or
`free_text` contains no moat language) → see Edge Cases.

### Step 3 — Tavily light-refresh (time-bounded)

Use `check-interpreter/scripts/run_tavily.py` with parameters:

```
start_date = f"{founding_year - 3}-01-01"
end_date   = f"{founding_year}-12-31"
queries    = queries.json (from tavily-query-builder)
```

U-check Tavily is **lighter than M-check** — 3-5 queries focused on
"in the candidate's claimed moat category, what's the incumbent moat
depth state at decision time, and is the category being commoditized?"

Save raw results to `tavily_u_results.json`.

### Step 4 — VRIO audit on dominant moat

Score the candidate's dominant moat across V / R / I / O using:
- `free_text` claim
- `candidate_card` evidence
- Tavily evidence (from Step 3) — used especially for R (rare?) and I
  (inimitable?) since those depend on incumbent landscape

For each dim record `verdict` (✓ / partial / ✗) and a one-line
`evidence` string citing the basis.

Compute `VRIO_score` = sum of 4 dim numeric scores.

### Step 5 — Extract erosion risks + incumbent moat depth

From the same Tavily batch, extract `erosion_risks` — known
commoditization signals / substitute threats / OSS displacement
indicators in the candidate's moat category, as of `founding_year`.

Determine **incumbent moat depth state**:

- **defensible** — incumbents in the same moat category maintain moat
  depth; category is not commoditizing in the retrieval window
- **eroding** — partial commoditization signal; moat is narrowing but
  still extant
- **commoditized** — moat category has lost defensibility (e.g. OSS
  alternatives dominate, multiple low-cost substitutes win share)

### Step 6 — Determine verdict

| Verdict | Conditions |
|---|---|
| **✅ PASS** | `VRIO_score ≥ 3.5/4.0` (4 dims essentially all ✓ or 3 ✓ + 1 partial) AND incumbent moat depth = `defensible` |
| **⚠ WARN** | `2.0 ≤ VRIO_score < 3.5` (e.g. 2-3 ✓ + 1-2 partial, or 3 ✓ + 1 ✗) OR incumbent moat depth = `eroding` |
| **🔴 FAIL** | `VRIO_score < 2.0` (≤1 ✓, mostly ✗ / partial) OR candidate makes no moat claim OR incumbent moat depth = `commoditized` |

### Step 7 — Write `u_check.json` (canonical schema)

```json
{
  "verdict":          "PASS | WARN | FAIL",
  "verdict_emoji":    "✅ | ⚠ | 🔴",
  "verdict_text":     "<one-line verdict summary>",
  "claimed_moats":    [
    {
      "moat_type":    "Network Economies | Scale Economies | Cornered Resource | Process Power | Brand | Switching Costs | Counter-positioning",
      "evidence":     "<from candidate_card.md moat dim + free_text>"
    }
  ],
  "vrio_breakdown": {
    "dominant_moat":  "<one of claimed_moats[].moat_type>",
    "valuable":       {"verdict": "✓ | ✗ | partial", "evidence": "<string>"},
    "rare":           {"verdict": "✓ | ✗ | partial", "evidence": "<string>"},
    "inimitable":     {"verdict": "✓ | ✗ | partial", "evidence": "<string>"},
    "organized":      {"verdict": "✓ | ✗ | partial", "evidence": "<string>"},
    "score":          "<0.0–4.0, ✓=1.0, partial=0.5, ✗=0.0, sum>"
  },
  "incumbent_moat_depth": "defensible | eroding | commoditized",
  "erosion_risks": ["<string>", "..."],
  "search_metadata": {
    "founding_year":      "<int>",
    "retrieval_window":   ["<YYYY-MM-DD>", "<YYYY-MM-DD>"],
    "tavily_query_count": "<integer>",
    "source_urls":        ["<url>", "..."]
  },
  "reasoning":            "<50–200 chars>"
}
```

**v1.4 → v1.5 schema simplification**: the `dominant_moat_required`
field is removed. In v1.4 it captured archetype-specific moat
requirements (e.g. Distribution requires Network Economies, Hardware-
vertical requires Cornered Resource). v1.5 drops archetype-conditional
moat preferences — VRIO audit alone determines moat strength.

## Edge cases

- **Candidate has no claimed moat**: `claimed_moats = []`, `vrio_breakdown
  = null` (or all ✗), `verdict = FAIL`, `reasoning = "no moat claim
  surfaced from candidate card or free-text → not defensible"`.
- **Multiple claimed moats, no clear dominant**: pick the moat with the
  strongest evidence in free-text as `dominant_moat`. List remaining in
  `claimed_moats[]`. Note in `reasoning`.
- **Moat type doesn't fit 7 Powers cleanly**: pick the closest match.
  Note the imperfect fit in `reasoning`. Do NOT invent new categories.
- **No Tavily data within window**: incumbent moat depth defaults to
  `eroding` (conservative), reasoning notes "insufficient window
  evidence". Verdict capped at ⚠ WARN.
- **`founding_year` missing**: halts U-check. Driver flags candidate as
  un-evaluable.

## Why no archetype-required moat type in v1.5

v1.4 mapped each archetype to a "required moat type" (Distribution →
Network Economies, Frontier-MO → Cornered Resource, Hardware-vertical →
Cornered Resource, etc.). If the candidate's claimed moat didn't cover
the archetype's required type, U-check penalized.

v1.5 drops this for two reasons:

1. **Methodology decoupling** (Decision A #3): archetype is aux output;
   the verdict path must not use archetype to filter / weight / branch.
   Required-moat-per-archetype is archetype-conditional, contradicts #3.
2. **Empirical noise**: the v1.4 mapping was thin — only Distribution
   and Frontier-model-owner had robust empirical evidence for their
   required moat type. The other 5 archetypes' required-moat assignments
   were assertion, not data-validated. Dropping cleans up unjustified
   signal.

Effect: VRIO audit alone is the moat-strength filter. Candidates whose
moat is robust on its own terms (high VRIO) pass regardless of moat
type; weak moats fail regardless.

## Why time-bounded retrieval matters (anti-leakage)

Same logic as M-check (see `m_check.md`). At decision time the investor
can see the moat-category landscape only up to `founding_year`. Tavily
results from after founding leak outcome: "incumbent X was acquired by
Y", "OSS commoditized the category", etc. Time-bounding to
`[founding_year - 3y, founding_year]` enforces decision-time honesty.
