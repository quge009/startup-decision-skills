"""Uniform Tavily query templates for the U-check.

v1.5 changes (vs v1.4):
- The same templates apply to every candidate without category branching.
- Time-bounded retrieval is enforced at execution time (start_date /
  end_date passed to Tavily client by run_tavily.py), not at query-text
  level. Templates remain time-neutral; the window is the wrapper's
  responsibility.

Placeholders use mustache double-brace `{{name}}` substituted by
build_queries.py from placeholders.json.
"""

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
