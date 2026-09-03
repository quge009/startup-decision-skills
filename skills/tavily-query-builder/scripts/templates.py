"""Tavily query templates for M-check + U-check (v1.5 archetype-agnostic).

v1.5 changes (vs v1.4):
- Uniform templates across all candidates — no per-archetype branching.
- Removed CANONICAL_ARCHETYPES / per-archetype dicts. Templates apply to
  every candidate regardless of archetype.
- Removed archetype-specific notes (e.g. "OSS-on-rented-GPU is anti-pattern").
- Time-bounded retrieval is enforced at execution time (start_date /
  end_date passed to Tavily client by run_tavily.py), not at query-text
  level. Templates remain time-neutral; the window is the wrapper's
  responsibility.

Placeholders use mustache double-brace `{{name}}` substituted by
build_queries.py from placeholders.json.
"""

# Canonical placeholders the templates expect. build_queries.py uses this
# list to validate placeholders.json shape; missing keys → `<null:name>`
# tokens in output queries → caught upstream as warnings.
CANONICAL_PLACEHOLDERS = [
    "candidate",
    "target_market",
    "sub_segment",
    "incumbent",
    "geographic_scope",
    "specialty_capability",
    "moat_claim",
]

# M-check uniform query templates (v1.5: same for all candidates).
# 5 queries cover: market size, incumbents + market share, growth trend,
# analyst-forecast triangulation, commoditization risk.
M_CHECK_TEMPLATES = {
    "queries": [
        "{{target_market}} market size forecast",
        "{{sub_segment}} top players market share incumbents",
        "{{sub_segment}} growth rate CAGR demand trend",
        "{{target_market}} TAM analyst Gartner IDC Sacra",
        "{{sub_segment}} commoditization OSS substitute risk",
    ],
    "extract_targets": [
        "Gartner / IDC / McKinsey / Sacra TAM forecast for target_market",
        "Peer-vendor ARR + valuation in target_market (for back-inference TAM)",
        "Incumbent occupancy of SAM (locked_pct)",
        "Porter Five Forces signal (rivalry / substitutes / new_entrants)",
    ],
    "note": (
        "Time-bounded retrieval window is applied at Tavily execution by "
        "run_tavily.py (start_date/end_date = [founding_year - 3y, founding_year]). "
        "Templates themselves are time-neutral."
    ),
}

# U-check uniform query templates (v1.5: same for all candidates).
# 4 queries cover: moat-category depth, candidate's specialty capability
# defensibility, erosion/commoditization risk, competitive landscape.
U_CHECK_TEMPLATES = {
    "queries": [
        "{{moat_claim}} {{target_market}} incumbent moat depth defensibility",
        "{{moat_claim}} {{specialty_capability}} barrier to imitation",
        "{{target_market}} {{moat_claim}} category erosion commoditization",
        "{{moat_claim}} competition {{geographic_scope}} landscape",
    ],
    "focus_notes": [
        "VRIO: Valuable — does the moat solve real customer pain?",
        "VRIO: Rare — is it scarce in incumbent landscape (Tavily evidence)?",
        "VRIO: Inimitable — what would competitors need to replicate (Tavily evidence)?",
        "VRIO: Organized — is the candidate set up to capture (free-text / card)?",
        "Erosion risks: OSS substitutes, low-cost alternatives, commoditization signals",
    ],
    "note": (
        "U-check Tavily is lighter than M-check (4 queries vs 5). "
        "Same time-bounded window applied at execution."
    ),
}
