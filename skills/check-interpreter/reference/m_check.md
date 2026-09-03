# M-check Framework (v1.5)

## Purpose

Reference doc for the `check-interpreter` skill when invoked with
`--check m`. Defines the market-sizing methodology — does the candidate
target a market large enough and healthy enough to support a viable
business?

**v1.5 status**: M-check is **archetype-agnostic** (per Decision A #3).
All candidates use the same M-check methodology, the same Tavily query
templates, and the same verdict thresholds. Archetype is NOT used to
condition the prompt or look up archetype-specific sub-segment baselines.

**v1.5 status**: M-check Tavily retrieval is **time-bounded**
(per Decision A #4). Tavily searches are restricted to the window
`[founding_year - 3y, founding_year]` so the M-check evaluates "what
could a decision-time investor have known about this market", not "what
do we know today knowing the outcome". This eliminates outcome leakage
on historical training candidates.

## Input

M-check consumes:

- `free_text.txt` — original proposal description
- `candidate_card.md` — 5-dim distillation (including `founding_year`)
- `archetype.json` — archetype metadata (read for the **`founding_year`**
  field if present, else read from candidate card; NOT used to branch
  prompts in v1.5)
- `queries.json` — Tavily query list from `tavily-query-builder`
- `tavily_m_results.json` — Tavily search results (executed by
  `check-interpreter/scripts/run_tavily.py` using
  `[founding_year - 3y, founding_year]` date window)

## Methodology overview

M-check assesses the candidate's market opportunity along three nested
layers — TAM (total addressable), SAM (serviceable addressable), SOM
(serviceable obtainable, after incumbents) — plus a Porter Five Forces
overlay for competitive pressure.

### TAM-SAM-SOM definitions

- **TAM** — the total dollar value of the broad market in which the
  candidate plays (e.g. "AI inference API market", "GPU NeoCloud market").
  Sourced externally from Gartner / IDC / McKinsey / Sacra or
  triangulated from peer-vendor ARR.
- **SAM** — the subset of TAM that the candidate could plausibly serve
  given its geographic / regulatory / customer-segment / tech-segment
  scope. Expressed as `pct_of_tam` (0.0–1.0) → `SAM = TAM × pct_of_tam`.
- **SOM** — what the candidate can realistically capture given
  incumbent occupancy of the SAM and competitive pressure.
  `SOM_headroom = SAM × (1 − locked_pct) × porter_discount`.

### Porter Five Forces overlay

Compresses SAM headroom by a single discount factor based on rivalry +
substitutes severity:

| Porter scenario | `porter_discount` |
|---|---|
| Rivalry not extreme, substitutes manageable | 1.0 (baseline) |
| Elevated rivalry **or** moderate substitutes | 0.7 |
| Extreme rivalry (e.g. heavy OSS commoditization in same space) OR severe substitutes (low-cost alt >50% share) | 0.5 |
| Both extreme (commoditized + multi-OSS displacement) | 0.3 |

## Step-by-step procedure

### Step 1 — Read inputs

Read `free_text.txt`, `candidate_card.md`, `archetype.json`,
`queries.json`. Extract `founding_year` from candidate card or archetype
metadata. Required for time-bounded retrieval (Step 2).

### Step 2 — Execute Tavily search (time-bounded)

Use `check-interpreter/scripts/run_tavily.py` with parameters:

```
start_date = f"{founding_year - 3}-01-01"
end_date   = f"{founding_year}-12-31"
queries    = queries.json (from tavily-query-builder)
```

Tavily filters results by publication date, returning only documents
published in `[founding_year - 3y, founding_year]`. This is the **anti-
leakage barrier** — without it, M-check would see post-decision news
(funding rounds, IPO, closure) that would trivially predict outcome.

Save raw results to `tavily_m_results.json`.

### Step 3a — Extract TAM (archetype-wide, NOT candidate-claimed)

From Tavily results, identify the **archetype-wide TAM** for the broad
market the candidate plays in. Triangulate two evidence types:

- **Peer empirical**: known players' ARR / valuation in the same market
  category, used to back-infer market TAM (e.g. HuggingFace $70M ARR,
  OpenRouter $1.3B valuation → infer model-marketplace TAM).
- **Analyst forecast**: Gartner / IDC / McKinsey / Sacra TAM forecasts
  for the relevant category.

Take envelope (union range or weighted average) → single TAM number ($B)
with range.

**DO NOT** trust candidate-framing TAM from `free_text.txt`. The
candidate's own framing is a methodologically-suspect anchor (incentive
to inflate); the framework's TAM must be externally triangulated.

### Step 3b — Derive `pct_of_tam` (candidate filter coefficient)

From candidate card 5-dim (especially customer + revenue model) and
free-text, extract candidate-specific filters:

- **Geographic scope** (CN-only / US-only / global / EU)
- **Regulatory scope** (SOC2 / FINRA / GDPR / 备案)
- **Customer-segment scope** (top-tier enterprise / SMB / consumer / gov)
- **Tech-segment scope** (specific OSS family / specific use case)

Compose into a single `pct_of_tam` (0.0–1.0). Apply once. **Do not
double-narrow** by also narrowing the TAM itself in Step 3a — that's a
common v1.0-era bug. `pct_of_tam` is the explicit filter coefficient.

### Step 3c — Extract `locked_pct` + Porter forces

From the same Tavily batch, extract:

- `locked_pct` — share of SAM already locked by incumbents (0.0–1.0)
- `rivalry`, `substitutes`, `new_entrants` (low / med / high each)
- `porter_discount` derived from rivalry + substitutes per the table above

### Step 4 — Compute SAM + SOM headroom

```
SAM ($B)          = TAM × pct_of_tam
SOM_headroom ($B) = SAM × (1 − locked_pct) × porter_discount
                  = TAM × pct_of_tam × (1 − locked_pct) × porter_discount
```

### Step 5 — Determine verdict

| Verdict | Conditions (all required) |
|---|---|
| **✅ PASS** | `SOM_headroom ≥ $1B` AND `porter_discount ≥ 0.7` (rivalry/substitutes not extreme) AND key data points published within window |
| **⚠ WARN** | `$100M ≤ SOM_headroom < $1B` OR `porter_discount == 0.5` (single elevated dimension) OR significant data gaps within window |
| **🔴 FAIL** | `SOM_headroom < $100M` OR `porter_discount == 0.3` (both rivalry+substitutes extreme) OR sub-segment lacks any empirical evidence in window |

**v1.4 → v1.5 simplification**: v1.4 had an additional "archetype-wide TAM
trajectory check" (compare current TAM to a 6-month-prior archive
baseline). v1.5 drops this — there's no per-archetype baseline archive
under archetype-agnostic methodology, and trajectory is implicitly
captured by the time-bounded window (data is intentionally from
`[founding-3y, founding]`, not "current" data).

### Step 6 — Write `m_check.json` (canonical schema)

```json
{
  "verdict":          "PASS | WARN | FAIL",
  "verdict_emoji":    "✅ | ⚠ | 🔴",
  "verdict_text":     "<one-line verdict summary>",
  "tam": {
    "value_b":        "<central estimate, $B>",
    "range_b":        ["<low>", "<high>"],
    "scope":          "<e.g. AI inference API market>",
    "source":         "<citation: Gartner / IDC / Sacra / peer-ARR-derived>"
  },
  "sam": {
    "subset_descriptor":  "<geo / regulatory / customer-segment filter>",
    "pct_of_tam":         "<0.0–1.0>",
    "value_b":            "<TAM × pct_of_tam, $B>"
  },
  "som": {
    "locked_pct":         "<0.0–1.0>",
    "porter_discount":    "0.3 | 0.5 | 0.7 | 1.0",
    "headroom_b":         "<TAM × pct_of_tam × (1 − locked_pct) × porter_discount, $B>"
  },
  "porter_forces": {
    "rivalry":            "low | med | high",
    "substitutes":        "low | med | high",
    "new_entrants":       "low | med | high",
    "buyer_power":        "low | med | high (optional)",
    "supplier_power":     "low | med | high (optional)"
  },
  "incumbents":          ["<top incumbent names, freeform string list>"],
  "search_metadata": {
    "founding_year":      "<int>",
    "retrieval_window":   ["<YYYY-MM-DD>", "<YYYY-MM-DD>"],
    "tavily_query_count": "<integer>",
    "source_urls":        ["<url>", "..."]
  },
  "reasoning":            "<50–200 chars, verdict rationale citing free-text + 5-dim + tavily evidence>"
}
```

## Edge cases

- **No Tavily data within window**: candidate is young enough that
  `founding_year - 3y` predates much of the relevant market discourse.
  Verdict capped at ⚠ WARN; reasoning notes "insufficient evidence in
  retrieval window".
- **`founding_year` missing or unparseable**: M-check halts. Pipeline
  driver should flag the candidate as un-evaluable; downstream aggregate
  treats missing M-check as FAIL.
- **Candidate-framed TAM disagrees materially with externally-triangulated
  TAM**: external triangulation wins (Step 3a constraint). Candidate's
  niche framing becomes `pct_of_tam` filter coefficient in Step 3b.
- **Brand-new sub-segment with no public TAM data in window**: use peer-
  vendor ARR back-inference + Sacra / Crunchbase private valuation
  triangulation. Tag `source` field as "peer-ARR-derived" so downstream
  consumers know the data is proxy. Verdict capped at ⚠ WARN unless
  multiple independent proxies converge.

## Why time-bounded retrieval matters (anti-leakage)

For historical training candidates (where outcome is known), Tavily today
returns articles about subsequent funding rounds, IPOs, closures — i.e.,
the outcome itself. An M-check that sees this data isn't predicting
market success from decision-time signals; it's reading the future. F0.5
on such data is meaningless.

Time-bounded retrieval enforces that M-check sees only data published in
`[founding_year - 3y, founding_year]`, modeling what a decision-time
investor could have known. The 3-year look-back captures pre-founding
market context (industry trends, competitor activity, customer demand
signals) without exposing post-founding outcomes.

For inference on new candidates (no known outcome yet), the same window
applies — search bounded at `[founding_year - 3y, founding_year]`. This
treats new and historical candidates symmetrically.
