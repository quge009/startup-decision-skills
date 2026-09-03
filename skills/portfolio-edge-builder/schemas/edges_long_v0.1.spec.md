# Schema: `edges_long_v0.1.parquet`

**Version**: v0.1.0
**Introduced**: W11 (2026-07-28)
**Purpose**: One row per (VC firm, portfolio company) edge scraped from a VC firm's public portfolio page. Sourced from the 20 top US VCs in `configs/top20_vc_portfolio.yaml` and produced by `scripts/ingest_vc_portfolio.py`.
**Companion**: none in v0.1 — per-edge provenance is inline (`source_page_url`, `selector_config_version`, `scraped_at`, `confidence` on the same row). If provenance grows richer in v0.2, will spin off into a long-format sibling.

## Design constraints inherited from `_PLAN.md` v0.1 principles

- **Additive schema evolution** — new fields only ADDED in later versions; existing fields never renamed / retyped / removed. `schema_version` per row.
- **Raw preservation** — every scraped VC portfolio page is stored under `raw/vc_portfolio/YYYY-MM-DD/<vc-slug>.html` verbatim before extraction. Re-extraction never requires re-scraping.
- **Universe-agnostic acquisition** — one dispatcher script (`ingest_vc_portfolio.py`) reads per-firm selector configs (`configs/portfolio_selectors/<vc-slug>.yaml`) + writes per-firm extracted parquets to `extracted/edges_by_source/<vc-slug>_v0.1.parquet`. Adding a new firm = adding a new selector yaml + a new row to `configs/top20_vc_portfolio.yaml`; existing firms unaffected.
- **Long-format edges + separate joined view** — `extracted/edges_by_source/*_v0.1.parquet` stores one file per VC (never merged). The unified `edges_long_v0.1.parquet` is materialized separately by `scripts/build_edges_long.py` (W15) and can be regenerated at any time from the per-VC files.

## File layout

- `/data/agents/investor-behavior-analysis/extracted/edges_by_source/<vc-slug>_v0.1.parquet` — per-VC extracted edges. `<vc-slug>` mirrors the slug already in `configs/top20_vc_portfolio.yaml`'s `investor_id` (e.g. `sequoia-capital`, `andreessen-horowitz`). One file per VC among the 17 static + 1 js_required firms (Benchmark / Tiger Global excluded — no_public / unclear). Overwritten on re-scrape (Q4 policy: latest scrape wins; scrape history lives in the raw HTML snapshot).
- `/data/agents/investor-behavior-analysis/edges_long_v0.1.parquet` — top-level merged view. Built by W15 from all per-VC files. Same schema as per-VC files.

## Columns

| # | Column | Type | Nullable | Description |
|---|---|---|---|---|
| 1 | `edge_id` | str (PK) | No | Composite key `<investor_id>::<company_normalized_name>`. Stable across re-scrapes (assuming normalization is deterministic). E.g. `findfunding:sequoia-capital::airbnb`. Uniqueness enforced within each per-VC parquet; the top-level `edges_long_v0.1.parquet` allows the SAME edge_id from multiple VCs only if `investor_id` differs (that would violate PK — see sanity invariants) |
| 2 | `investor_id` | str | No | Canonical investor id, FK to `investors_entity_v0.1.parquet`. E.g. `findfunding:sequoia-capital` |
| 3 | `investor_name` | str | No | Denormalized firm name (from investors_entity's canonical `name`) for readability. Not used for joins |
| 4 | `company_name_raw` | str | No | Portfolio company name exactly as it appeared on the VC's portfolio page (post text-content extraction of the matched selector, no normalization) |
| 5 | `company_normalized_name` | str | No | Dedup key within each VC. Same normalization rules as `investors_entity_v0.1.spec.md` §normalized_name: lowercased, trailing corporate suffix tokens (`Ventures / Capital / Partners / Fund / LLC / LP / Inc / Group / Advisors / Holdings / Investments / Management / Corp / Co / Corporation / Company`) stripped, whitespace collapsed. Strip list harmonized 2026-07-30 (W20 v3) — see `_common.py` |
| 6 | `company_url` | str | Yes | External URL for the portfolio company (usually the company's own website), if the VC's page linked to one. Some portfolio pages are logo-only with no outbound link — null then |
| 7 | `company_domain` | str | Yes | Registrable domain extracted from `company_url` (public suffix stripped). Used for future company-canonicalization joins (Step-3 will introduce `companies_entity_v0.1.parquet`) |
| 8 | `stage_at_investment` | list\<str\> | Yes | Stage(s) at which this VC invested, IF the portfolio page groups by stage (e.g. a16z tags portfolio companies by fund cohort / stage; Sequoia doesn't). Normalized to the 10-value enum from `configs/universe_v0.1.yaml scope.investment_stages`. `list<str>` (not scalar) because some VCs list a company under multiple stages if they participated in multiple rounds |
| 9 | `industry` | list\<str\> | Yes | Industry / vertical tag(s) if the portfolio page groups by industry (e.g. Bessemer groups by "Cybersecurity" / "Fintech" / etc). Free-text in v0.1 — no normalization or controlled vocabulary. `list<str>` because a company can span multiple verticals |
| 10 | `position` | str | Yes | Portfolio-lifecycle status if the page distinguishes: `active` (current portfolio company), `exited` (generic exit), `acquired` (M&A exit), `ipo` (public-market exit), `closed` (shut down / failed). Null if the page doesn't distinguish (most VCs show current + past on the same list without labels). Scalar because a company is in exactly one lifecycle state at a given scrape time |
| 11 | `source_page_url` | str | No | The specific URL scraped for this edge (e.g. `https://www.sequoiacap.com/companies` — mirrors `configs/top20_vc_portfolio.yaml`'s `portfolio_url` for this VC). Same for all rows produced from one scrape run |
| 12 | `selector_config_version` | str | No | Which version of the per-firm selector yaml (`configs/portfolio_selectors/<vc-slug>.yaml`'s top-level `version:` key) produced this edge. Bumped on selector edits so downstream can filter to a specific extraction lineage |
| 13 | `scraped_at` | date | No | Date the VC's portfolio page was fetched. All rows in a given per-VC parquet share this date (since a scrape run overwrites the parquet — Q4 policy) |
| 14 | `schema_version` | str | No | Constant `"v0.1.0"` |
| 15 | `confidence` | float | No | 0.0-1.0 confidence that this (investor, company) edge is correct. Default per-tech_flag baselines: `static` (clean selector on non-SPA) 0.95; `js_required` (Playwright / tavily.extract rendered) 0.90; `unclear` (per-firm human eyeball or heuristic) 0.75. Individual selectors can override per config (e.g. a selector known to sometimes catch nav-menu links may set 0.85) |

## Position value definitions

- **`active`** — company is currently in the VC's active portfolio (not exited, not closed). Default assumption for any portfolio-page entry without an explicit exit label.
- **`exited`** — company has exited via any means; use only when the page groups exits without further distinguishing between acquisition and IPO.
- **`acquired`** — company was acquired by another entity (M&A).
- **`ipo`** — company went public.
- **`closed`** — company shut down or failed. Rarely explicit on portfolio pages (VCs typically remove failed investments from public display).

## Deduplication

- **Within a per-VC parquet**: `edge_id` is unique (PK). If the same VC's portfolio page lists a company under two different stages / industries / cards, the multiple selector hits are unioned into a single row (stage_at_investment / industry lists are the unions across all hits; position picks the most-recent lifecycle if inconsistent). This is the extraction logic's responsibility (in `ingest_vc_portfolio.py`), not a post-hoc merge.
- **Across VCs** (in `edges_long_v0.1.parquet`): `edge_id` may repeat with the SAME company_normalized_name across different investor_ids — that's expected (multi-VC investments generate one row per VC). `(investor_id, edge_id)` is the effective composite PK at the top-level file. Sanity invariant enforces this.
- **Cross-VC company entity canonicalization** (mapping the multiple "Airbnb" rows from Sequoia + Founders Fund + Greylock to a single canonical company entity with a `company_id`): **DEFERRED to M1-Step-3**. Rationale: Step-3 introduces SEC EDGAR Form D filings, which are company-centric (issuer + amount + date); canonicalizing companies there naturally covers both scraped-portfolio-edges (this table) and Form D events under one company entity table. v0.1 leaves company-side identity as just `company_normalized_name` + `company_domain`.

## Sanity invariants (checked by `ingest_vc_portfolio.py` per-VC + `build_edges_long.py` at merge time)

Per-VC parquet (`extracted/edges_by_source/<vc-slug>_v0.1.parquet`):

- `edge_id` is unique within the file.
- `investor_id` is constant across the file (all rows are that one VC's edges).
- Row count is within `[configs/portfolio_selectors/<vc-slug>.yaml].sanity_checks.min_edges / max_edges` — bounds set per firm (Sequoia expects 300+, First Round ~500, GV ~600 based on public estimates; each selector yaml declares its own bounds).
- `stage_at_investment` (when non-empty) — every element is in the 10-value stage enum from `configs/universe_v0.1.yaml`.
- `position` (when non-null) — one of `{active, exited, acquired, ipo, closed}`.
- `schema_version` constant `"v0.1.0"`.

Top-level `edges_long_v0.1.parquet`:

- `(investor_id, edge_id)` composite is unique across the file.
- Every `investor_id` appears in `investors_entity_v0.1.parquet` (FK integrity).
- Every per-VC file's row count sums to this file's row count (no rows lost in merge).

## What is intentionally NOT in v0.1

Deferred to v0.2+ or Step-3+:

- `company_id` — canonical company entity id. Introduced in M1-Step-3 when Form D filings force company-side entity resolution.
- `funding_amount_usd`, `funding_date`, `funding_round_id` — funding-event fields. Come from Form D (Step-3), not portfolio pages.
- `lead_or_follow`, `board_seat`, `partner_lead_name` — investment-role fields. Deferred; some VCs surface this on the portfolio page (e.g. Sequoia lists lead partner) and could be captured in v0.2.
- `announcement_url`, `announcement_date` — the "when did this VC announce investment in this company" news URL. Requires Tavily news enrichment (Step-3+).
- `edge_provenance_long` companion table — v0.1's inline provenance (source_page_url + selector_config_version + scraped_at + confidence per row) is enough. A long-format sibling would be needed if v0.2 introduces per-field confidence or multi-source cross-verification.

## Change log

- **v0.1.0 (2026-07-28, W11)**: Initial schema. User-signed Q1-Q4 decisions: per-VC extracted parquets + top-level combined (Q1-A); no `company_id` v0.1, defer to Step-3 (Q2-B); include `stage_at_investment` / `industry` / `position` as nullable (Q3-A); per-VC parquet overwritten on re-scrape, latest wins (Q4-A). Schema is 15 columns.
