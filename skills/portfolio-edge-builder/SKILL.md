---
name: portfolio-edge-builder
description: Build auditable investor-to-company portfolio edges from cached public portfolio-page HTML by authoring and validating per-site CSS selectors, extracting per-firm Parquet tables, checking quality, and merging them under a common schema. Use for public portfolio reconstruction; do not use for sites whose content is unavailable in the supplied HTML.
---

# Portfolio Edge Builder

Turn cached public portfolio pages into a reproducible investor-to-company edge
table. Preserve the source HTML and selector YAML as provenance. Never infer a
company that is absent from the supplied page.

## Workflow

1. Resolve firm metadata with `scripts/lookup_firm.py` and the newest cached HTML
   with `scripts/resolve_html_path.py`.
2. Inspect one repeated company-entry DOM structure and author a selector YAML.
   Include `entry_selector`, `company_name_raw`, optional URL/stage/industry/
   position selectors, sanity bounds, confidence, and `_generation_notes`.
3. Run `scripts/self_test_selector.py --yaml ... --html ... --write-back`. Revise
   at most three times; record a null selector and explanation if the page cannot
   be represented safely.
4. Run `scripts/ingest_vc_portfolio.py` against the cached HTML and selector
   directory to write one source Parquet per investor. Supply the firm list and
   selector directory explicitly.
5. Run `scripts/spot_check_edges.py` before accepting a source. Investigate
   navigation text, URL fragments, duplicate names, implausible counts, and
   systematically null optional fields.
6. Run `scripts/build_edges_long.py --investors ...` to merge accepted source
   tables and verify uniqueness, investor foreign keys, and row-count
   reconciliation.

Read `schemas/edges_long_v0.1.spec.md` before changing fields or types. All
project-specific firm lists, selector YAMLs, HTML caches, and output directories
are caller-supplied; they are evidence, not part of this reusable skill.

## Requirements

Python 3.10+, `pyarrow`, `beautifulsoup4`, `lxml`, and `PyYAML`.
