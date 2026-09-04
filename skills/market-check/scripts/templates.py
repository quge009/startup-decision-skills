"""Uniform Tavily query templates for the M-check.

v1.5 changes (vs v1.4):
- The same templates apply to every candidate without category branching.
- Time-bounded retrieval is enforced at execution time (start_date /
  end_date passed to Tavily client by run_tavily.py), not at query-text
  level. Templates remain time-neutral; the window is the wrapper's
  responsibility.

Placeholders use mustache double-brace `{{name}}` substituted by
build_queries.py from placeholders.json.
"""

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
